"""Tests for the live-set-filters ingestion capability.

Covers:
- HookConfigResolver.update_destinations() cache invalidation (config_resolver.py)
- context_intelligence.set_ingestion_filters capability: live exclude take-effect,
  disk-consistency reporting
- context_intelligence.set_ingestion_filters patching the in-memory session config
  snapshot inherited by future sub-sessions (_patch_inherited_hook_config)
- context_intelligence.verify_ingestion_consistency fail-loud in both directions
- _patch_inherited_hook_config as a standalone unit
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml

from amplifier_module_hook_context_intelligence.config_resolver import HookConfigResolver
from tests.helpers import make_lifecycle_coordinator, mount_and_ready


def _bare_resolver_coordinator() -> MagicMock:
    """A minimal coordinator double for direct HookConfigResolver construction."""
    coordinator = MagicMock()
    coordinator.config = {}
    coordinator.get_capability = MagicMock(return_value=None)
    return coordinator


def _write_settings(path: Path, destinations: dict[str, Any]) -> str:
    """Write a settings.yaml in the real overrides.hook-context-intelligence.config shape."""
    doc = {
        "overrides": {
            "hook-context-intelligence": {
                "config": {"destinations": destinations},
            }
        }
    }
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# A. update_destinations cache invalidation
# ---------------------------------------------------------------------------
class TestUpdateDestinationsCacheInvalidation:
    def test_destinations_property_reflects_new_block_after_update(self) -> None:
        coordinator = _bare_resolver_coordinator()
        initial = {"d1": {"url": "http://d1", "api_key": "k1", "include": ["**"], "exclude": []}}
        resolver = HookConfigResolver({"destinations": initial}, coordinator)

        cached = resolver.destinations
        assert cached["d1"].exclude == ()

        new_raw = {
            "d1": {
                "url": "http://d1",
                "api_key": "k1",
                "include": ["**"],
                "exclude": ["/tmp/foo/**"],
            }
        }
        resolver.update_destinations(new_raw)

        refreshed = resolver.destinations
        assert refreshed["d1"].exclude == ("/tmp/foo/**",)
        assert refreshed is not cached, "destinations must be re-derived, not the stale cached dict"
        # The old cached reference itself must never mutate in place either.
        assert cached["d1"].exclude == ()

    def test_validate_destinations_reflects_new_block_after_update(self) -> None:
        coordinator = _bare_resolver_coordinator()
        initial = {"d1": {"url": "http://d1", "api_key": "k1", "include": ["**"], "exclude": []}}
        resolver = HookConfigResolver({"destinations": initial}, coordinator)
        assert resolver.validate_destinations()["d1"].exclude == ()

        new_raw = {
            "d1": {
                "url": "http://d1",
                "api_key": "k1",
                "include": ["**"],
                "exclude": ["/tmp/foo/**"],
            }
        }
        resolver.update_destinations(new_raw)

        assert resolver.validate_destinations()["d1"].exclude == ("/tmp/foo/**",)

    def test_update_destinations_can_add_a_new_destination(self) -> None:
        coordinator = _bare_resolver_coordinator()
        resolver = HookConfigResolver({"destinations": {}}, coordinator)
        assert resolver.destinations == {}

        resolver.update_destinations(
            {"new-dest": {"url": "http://new", "api_key": "k", "include": ["**"], "exclude": []}}
        )

        assert set(resolver.destinations) == {"new-dest"}


# ---------------------------------------------------------------------------
# B. set_ingestion_filters excludes a destination live
# ---------------------------------------------------------------------------
class TestSetIngestionFiltersExcludesDestinationLive:
    async def test_set_filters_updates_active_destinations(self, tmp_path: Path) -> None:
        working_dir = str(tmp_path)
        config = {
            "destinations": {
                "d1": {"url": "http://d1", "api_key": "k1", "include": ["**"], "exclude": []},
                "d2": {"url": "http://d2", "api_key": "k2", "include": ["**"], "exclude": []},
            }
        }
        coordinator = make_lifecycle_coordinator(working_dir=working_dir)
        cleanup = await mount_and_ready(coordinator, config)
        try:
            set_filters = coordinator.get_capability("context_intelligence.set_ingestion_filters")
            assert set_filters is not None

            new_raw = {
                "d1": {"url": "http://d1", "api_key": "k1", "include": ["**"], "exclude": ["**"]},
                "d2": {"url": "http://d2", "api_key": "k2", "include": ["**"], "exclude": []},
            }
            report = await set_filters(raw_destinations=new_raw)

            assert "d1" not in report["active"]
            assert "d2" in report["active"]
            assert report["destinations"]["d1"]["exclude"] == ["**"]
            assert report["destinations"]["d2"]["exclude"] == []
            assert report["match_key"]
        finally:
            await cleanup()

    async def test_disk_consistent_is_none_without_settings_path(self, tmp_path: Path) -> None:
        working_dir = str(tmp_path)
        config = {
            "destinations": {
                "d1": {"url": "http://d1", "api_key": "k1", "include": ["**"], "exclude": []},
            }
        }
        coordinator = make_lifecycle_coordinator(working_dir=working_dir)
        cleanup = await mount_and_ready(coordinator, config)
        try:
            set_filters = coordinator.get_capability("context_intelligence.set_ingestion_filters")
            new_raw = {
                "d1": {"url": "http://d1", "api_key": "k1", "include": ["**"], "exclude": ["**"]},
            }

            # Default verify_disk=True but no settings_path given -> the disk
            # cross-check never runs; disk_consistent stays None either way.
            report = await set_filters(raw_destinations=new_raw)
            assert report["disk_consistent"] is None

            report_no_verify = await set_filters(raw_destinations=new_raw, verify_disk=False)
            assert report_no_verify["disk_consistent"] is None
        finally:
            await cleanup()

    async def test_set_filters_requires_a_destinations_source(self, tmp_path: Path) -> None:
        working_dir = str(tmp_path)
        config = {"destinations": {}}
        coordinator = make_lifecycle_coordinator(working_dir=working_dir)
        cleanup = await mount_and_ready(coordinator, config)
        try:
            set_filters = coordinator.get_capability("context_intelligence.set_ingestion_filters")
            with pytest.raises(ValueError):
                await set_filters()
        finally:
            await cleanup()


# ---------------------------------------------------------------------------
# C. set_ingestion_filters patches the inherited session config snapshot
# ---------------------------------------------------------------------------
class TestSetIngestionFiltersPatchesInheritedSnapshot:
    async def test_inherited_hook_config_patched(self, tmp_path: Path) -> None:
        working_dir = str(tmp_path)
        initial_raw = {
            "d1": {"url": "http://d1", "api_key": "k1", "include": ["**"], "exclude": []},
        }
        config = {"destinations": initial_raw}
        coordinator = make_lifecycle_coordinator(working_dir=working_dir)
        hooks_list = [
            {"module": "hook-context-intelligence", "config": {"destinations": initial_raw}},
            {"module": "other-hook", "config": {"foo": "bar"}},
        ]
        coordinator.session = SimpleNamespace(config={"hooks": hooks_list})

        cleanup = await mount_and_ready(coordinator, config)
        try:
            set_filters = coordinator.get_capability("context_intelligence.set_ingestion_filters")
            new_raw = {
                "d1": {"url": "http://d1", "api_key": "k1", "include": ["**"], "exclude": ["**"]},
            }
            report = await set_filters(raw_destinations=new_raw)

            assert report["inherited_snapshot_patched"] is True
            ci_entry = next(h for h in hooks_list if h["module"] == "hook-context-intelligence")
            assert ci_entry["config"]["destinations"] == new_raw
            other_entry = next(h for h in hooks_list if h["module"] == "other-hook")
            assert other_entry["config"] == {"foo": "bar"}
        finally:
            await cleanup()

    async def test_inherited_snapshot_not_patched_without_a_real_session_config(
        self, tmp_path: Path
    ) -> None:
        """No .session.config dict wired up -> patch is a no-op, reported honestly."""
        working_dir = str(tmp_path)
        config = {
            "destinations": {
                "d1": {"url": "http://d1", "api_key": "k1", "include": ["**"], "exclude": []},
            }
        }
        coordinator = make_lifecycle_coordinator(working_dir=working_dir)
        # coordinator.session is left as an auto-vivified MagicMock attribute here
        # (not a real dict-backed session config) -> _patch_inherited_hook_config
        # must report False rather than silently pretending it patched anything.
        cleanup = await mount_and_ready(coordinator, config)
        try:
            set_filters = coordinator.get_capability("context_intelligence.set_ingestion_filters")
            report = await set_filters(raw_destinations=config["destinations"])
            assert report["inherited_snapshot_patched"] is False
        finally:
            await cleanup()


# ---------------------------------------------------------------------------
# D. verify_ingestion_consistency fail-loud both directions
# ---------------------------------------------------------------------------
class TestVerifyIngestionConsistency:
    async def test_live_patched_but_disk_not_written_raises(self, tmp_path: Path) -> None:
        working_dir = str(tmp_path)
        config = {
            "destinations": {
                "d1": {"url": "http://d1", "api_key": "k1", "include": ["**"], "exclude": []},
            }
        }
        coordinator = make_lifecycle_coordinator(working_dir=working_dir)
        cleanup = await mount_and_ready(coordinator, config)
        try:
            resolver = coordinator.get_capability("context_intelligence.hook_config_resolver")
            resolver.update_destinations(
                {
                    "d1": {
                        "url": "http://d1",
                        "api_key": "k1",
                        "include": ["**"],
                        "exclude": ["**"],
                    }
                }
            )

            settings_path = _write_settings(
                tmp_path / "settings.yaml",
                {"d1": {"url": "http://d1", "api_key": "k1", "include": ["**"], "exclude": []}},
            )

            verify = coordinator.get_capability("context_intelligence.verify_ingestion_consistency")
            with pytest.raises(RuntimeError, match="live ingestion filter disagrees"):
                verify(settings_path)
        finally:
            await cleanup()

    async def test_disk_has_exclude_live_does_not_raises(self, tmp_path: Path) -> None:
        working_dir = str(tmp_path)
        config = {
            "destinations": {
                "d1": {"url": "http://d1", "api_key": "k1", "include": ["**"], "exclude": []},
            }
        }
        coordinator = make_lifecycle_coordinator(working_dir=working_dir)
        cleanup = await mount_and_ready(coordinator, config)
        try:
            settings_path = _write_settings(
                tmp_path / "settings.yaml",
                {"d1": {"url": "http://d1", "api_key": "k1", "include": ["**"], "exclude": ["**"]}},
            )
            verify = coordinator.get_capability("context_intelligence.verify_ingestion_consistency")
            with pytest.raises(RuntimeError, match="live ingestion filter disagrees"):
                verify(settings_path)
        finally:
            await cleanup()

    async def test_live_and_disk_agree_returns_consistent(self, tmp_path: Path) -> None:
        working_dir = str(tmp_path)
        config = {
            "destinations": {
                "d1": {"url": "http://d1", "api_key": "k1", "include": ["**"], "exclude": []},
            }
        }
        coordinator = make_lifecycle_coordinator(working_dir=working_dir)
        cleanup = await mount_and_ready(coordinator, config)
        try:
            settings_path = _write_settings(
                tmp_path / "settings.yaml",
                {"d1": {"url": "http://d1", "api_key": "k1", "include": ["**"], "exclude": []}},
            )
            verify = coordinator.get_capability("context_intelligence.verify_ingestion_consistency")
            result = verify(settings_path)

            assert result["consistent"] is True
            assert result["live_exclude"] == {"d1": []}
            assert result["disk_exclude"] == {"d1": []}
        finally:
            await cleanup()


# ---------------------------------------------------------------------------
# E. _patch_inherited_hook_config unit
# ---------------------------------------------------------------------------
class TestPatchInheritedHookConfigUnit:
    def test_patches_matching_hook_entry(self) -> None:
        from amplifier_module_hook_context_intelligence import _patch_inherited_hook_config

        hooks_list = [
            {"module": "hook-context-intelligence", "config": {"destinations": {"old": {}}}},
            {"module": "other-hook", "config": {"foo": "bar"}},
        ]
        coordinator = SimpleNamespace(session=SimpleNamespace(config={"hooks": hooks_list}))

        new_raw = {"d1": {"url": "http://d1", "api_key": "k1"}}
        patched = _patch_inherited_hook_config(coordinator, new_raw)

        assert patched is True
        ci_entry = next(h for h in hooks_list if h["module"] == "hook-context-intelligence")
        assert ci_entry["config"]["destinations"] == new_raw
        other_entry = next(h for h in hooks_list if h["module"] == "other-hook")
        assert other_entry["config"] == {"foo": "bar"}

    def test_returns_false_when_no_session_or_config(self) -> None:
        from amplifier_module_hook_context_intelligence import _patch_inherited_hook_config

        class Empty:
            pass

        assert _patch_inherited_hook_config(Empty(), {"a": {}}) is False

    def test_returns_false_when_hooks_not_a_list(self) -> None:
        from amplifier_module_hook_context_intelligence import _patch_inherited_hook_config

        coordinator = SimpleNamespace(session=SimpleNamespace(config={"hooks": "not-a-list"}))
        assert _patch_inherited_hook_config(coordinator, {"a": {}}) is False

    def test_returns_false_when_no_matching_hook_entry(self) -> None:
        from amplifier_module_hook_context_intelligence import _patch_inherited_hook_config

        coordinator = SimpleNamespace(
            session=SimpleNamespace(config={"hooks": [{"module": "other-hook", "config": {}}]})
        )
        assert _patch_inherited_hook_config(coordinator, {"a": {}}) is False

    def test_falls_back_to_coordinator_config_when_no_session(self) -> None:
        from amplifier_module_hook_context_intelligence import _patch_inherited_hook_config

        hooks_list = [{"module": "hook-context-intelligence", "config": {}}]
        coordinator = SimpleNamespace(config={"hooks": hooks_list})

        new_raw = {"d1": {"url": "http://d1"}}
        assert _patch_inherited_hook_config(coordinator, new_raw) is True
        assert hooks_list[0]["config"]["destinations"] == new_raw


# ---------------------------------------------------------------------------
# F. Regression: the disk cross-check must not destroy the report, and must not
#    fault a filter that is correct but declared across settings scopes.
#    (ci_delete_fixes-zbx, ci_delete_fixes-1j2)
# ---------------------------------------------------------------------------
class TestDiskCheckIsReportedNotRaised:
    """The swap happens BEFORE the cross-check. Raising past it threw away the
    only record of what went live, leaving callers unable to tell "nothing was
    applied" from "applied but unattested" -- and destroying the
    inherited_snapshot_patched / active fields needed to diagnose ingestion."""

    async def test_mismatch_returns_report_instead_of_raising(self, tmp_path: Path) -> None:
        working_dir = str(tmp_path)
        settings_path = tmp_path / "settings.yaml"
        # Disk declares d1 with NO exclude...
        settings_path.write_text(
            yaml.safe_dump(
                {
                    "overrides": {
                        "hook-context-intelligence": {
                            "config": {
                                "destinations": {
                                    "d1": {
                                        "url": "http://d1",
                                        "api_key": "k1",
                                        "include": ["**"],
                                        "exclude": [],
                                    }
                                }
                            }
                        }
                    }
                }
            )
        )
        config = {
            "destinations": {
                "d1": {"url": "http://d1", "api_key": "k1", "include": ["**"], "exclude": []},
            }
        }
        coordinator = make_lifecycle_coordinator(working_dir=working_dir)
        cleanup = await mount_and_ready(coordinator, config)
        try:
            set_filters = coordinator.get_capability("context_intelligence.set_ingestion_filters")
            # ...but we apply a live-only exclude, so live and disk genuinely differ.
            live_only = {
                "d1": {"url": "http://d1", "api_key": "k1", "include": ["**"], "exclude": ["**"]},
            }
            report = await set_filters(
                raw_destinations=live_only,
                settings_path=str(settings_path),
                verify_disk=True,
            )

            # Did NOT raise, and the report survived.
            assert report["disk_consistent"] is False
            assert report["disk_check"]["mismatched"]["d1"] == {"live": ["**"], "disk": []}
            # The fields needed to diagnose what actually went live are present.
            assert "active" in report
            assert "match_key" in report
            assert "inherited_snapshot_patched" in report
            assert report["destinations"]["d1"]["exclude"] == ["**"]
        finally:
            await cleanup()

    async def test_destination_from_another_settings_scope_is_unverified_not_a_fault(
        self, tmp_path: Path
    ) -> None:
        """Live config is the kernel's MERGED settings; the cross-check reads ONE
        file. A destination declared in another scope must be reported as
        unverified, never as an inconsistency."""
        working_dir = str(tmp_path)
        settings_path = tmp_path / "settings.yaml"
        # The file knows about d1 only.
        settings_path.write_text(
            yaml.safe_dump(
                {
                    "overrides": {
                        "hook-context-intelligence": {
                            "config": {
                                "destinations": {
                                    "d1": {
                                        "url": "http://d1",
                                        "api_key": "k1",
                                        "include": ["**"],
                                        "exclude": ["**"],
                                    }
                                }
                            }
                        }
                    }
                }
            )
        )
        config = {
            "destinations": {
                "d1": {"url": "http://d1", "api_key": "k1", "include": ["**"], "exclude": []},
            }
        }
        coordinator = make_lifecycle_coordinator(working_dir=working_dir)
        cleanup = await mount_and_ready(coordinator, config)
        try:
            set_filters = coordinator.get_capability("context_intelligence.set_ingestion_filters")
            # Live carries d1 (matching the file) PLUS d2 from a different scope.
            merged = {
                "d1": {"url": "http://d1", "api_key": "k1", "include": ["**"], "exclude": ["**"]},
                "d2": {"url": "http://d2", "api_key": "k2", "include": ["**"], "exclude": ["**"]},
            }
            report = await set_filters(
                raw_destinations=merged,
                settings_path=str(settings_path),
                verify_disk=True,
            )

            assert report["disk_consistent"] is True
            assert report["disk_check"]["unverified"] == ["d2"]
            assert report["disk_check"]["mismatched"] == {}
        finally:
            await cleanup()

    async def test_verify_only_does_not_fault_on_other_scope_destinations(
        self, tmp_path: Path
    ) -> None:
        working_dir = str(tmp_path)
        settings_path = tmp_path / "settings.yaml"
        settings_path.write_text(
            yaml.safe_dump(
                {
                    "overrides": {
                        "hook-context-intelligence": {
                            "config": {
                                "destinations": {
                                    "d1": {
                                        "url": "http://d1",
                                        "api_key": "k1",
                                        "include": ["**"],
                                        "exclude": [],
                                    }
                                }
                            }
                        }
                    }
                }
            )
        )
        config = {
            "destinations": {
                "d1": {"url": "http://d1", "api_key": "k1", "include": ["**"], "exclude": []},
                "d2": {"url": "http://d2", "api_key": "k2", "include": ["**"], "exclude": []},
            }
        }
        coordinator = make_lifecycle_coordinator(working_dir=working_dir)
        cleanup = await mount_and_ready(coordinator, config)
        try:
            verify = coordinator.get_capability("context_intelligence.verify_ingestion_consistency")
            result = verify(str(settings_path))
            assert result["consistent"] is True
            assert result["unverified"] == ["d2"]
        finally:
            await cleanup()


# ---------------------------------------------------------------------------
# G. Regression: a destination validation DROPPED as misconfigured is not a
#    filter inconsistency.  (ci_delete_fixes-9fw)
#
#    Measured in a real session: mismatched={} yet consistent=false, purely
#    because two misconfigured destinations ("missing url; api_key is unusable")
#    were absent from the live filter. That is a destination-config problem, not
#    a stale exclude -- and one unexpanded ${VAR} is enough to trigger it.
# ---------------------------------------------------------------------------
class TestDroppedDestinationIsNotAFilterFault:
    async def test_misconfigured_destination_does_not_make_the_filter_inconsistent(
        self, tmp_path: Path
    ) -> None:
        working_dir = str(tmp_path)
        settings_path = tmp_path / "settings.yaml"
        # The file declares a good destination AND a broken one (no url).
        settings_path.write_text(
            yaml.safe_dump(
                {
                    "overrides": {
                        "hook-context-intelligence": {
                            "config": {
                                "destinations": {
                                    "good": {
                                        "url": "http://good",
                                        "api_key": "k",
                                        "include": ["**"],
                                        "exclude": ["**"],
                                    },
                                    "broken": {"api_key": "k", "include": ["**"], "exclude": []},
                                }
                            }
                        }
                    }
                }
            )
        )
        config = {
            "destinations": {
                "good": {"url": "http://good", "api_key": "k", "include": ["**"], "exclude": []},
            }
        }
        coordinator = make_lifecycle_coordinator(working_dir=working_dir)
        cleanup = await mount_and_ready(coordinator, config)
        try:
            set_filters = coordinator.get_capability("context_intelligence.set_ingestion_filters")
            new_raw = {
                "good": {
                    "url": "http://good",
                    "api_key": "k",
                    "include": ["**"],
                    "exclude": ["**"],
                },
                "broken": {"api_key": "k", "include": ["**"], "exclude": []},  # no url -> dropped
            }
            report = await set_filters(
                raw_destinations=new_raw,
                settings_path=str(settings_path),
                verify_disk=True,
            )

            check = report["disk_check"]
            assert check["mismatched"] == {}
            # 'broken' is absent from live because validation rejected it --
            # reported under its own key, and NOT a consistency fault.
            assert check["dropped_by_validation"] == ["broken"]
            assert check["missing_live"] == []
            assert report["disk_consistent"] is True
        finally:
            await cleanup()

    async def test_genuinely_absent_destination_still_reports_inconsistent(
        self, tmp_path: Path
    ) -> None:
        """The fix must not blunt the real case: declared on disk, valid, but
        simply not in the live filter."""
        from amplifier_module_hook_context_intelligence import _compare_exclude_maps

        check = _compare_exclude_maps(
            live={"good": []},
            disk={"good": [], "absent": ["**"]},
            dropped=set(),  # nothing was dropped by validation
        )
        assert check["missing_live"] == ["absent"]
        assert check["dropped_by_validation"] == []
        assert check["consistent"] is False
