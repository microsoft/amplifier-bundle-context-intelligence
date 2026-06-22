"""Tests for fanout.py — pure pathspec-based destination selection (D5/D6/D7/D8/S3)."""

from __future__ import annotations

import pytest

from amplifier_module_hook_context_intelligence.config_resolver import Destination
from amplifier_module_hook_context_intelligence.fanout import (
    destination_is_active,
    normalize_match_key,
    select_active,
)


# ---------------------------------------------------------------------------
# normalize_match_key
# ---------------------------------------------------------------------------
class TestNormalizeMatchKey:
    def test_absolute_path_returned_as_posix_with_trailing_slash(self) -> None:
        # working_dir is always a directory; the key carries a trailing slash so
        # pathspec applies .gitignore directory semantics (see normalize_match_key).
        result = normalize_match_key("/home/user/repos/app")
        assert result == "/home/user/repos/app/"

    def test_filesystem_root_not_double_slashed(self) -> None:
        assert normalize_match_key("/") == "/"

    def test_tilde_expanded(self) -> None:
        result = normalize_match_key("~/repos/app")
        assert "~" not in result
        assert result.endswith("/")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            normalize_match_key("")

    def test_none_like_raises(self) -> None:
        with pytest.raises((ValueError, AttributeError)):
            normalize_match_key(None)  # type: ignore[arg-type]

    def test_returns_absolute_path(self, tmp_path) -> None:
        result = normalize_match_key(str(tmp_path))
        assert result.startswith("/")


# ---------------------------------------------------------------------------
# pathspec matching
# ---------------------------------------------------------------------------
class TestPathspecMatching:
    def test_double_star_matches_any_path(self) -> None:
        dest = Destination(name="all", url="http://x:8000", api_key="k", include=("**",))
        assert destination_is_active(dest, "/home/user/repos/client-x/app")

    def test_double_star_matches_root(self) -> None:
        dest = Destination(name="all", url="http://x:8000", api_key="k", include=("**",))
        assert destination_is_active(dest, "/tmp")

    def test_pattern_matches_matching_path(self) -> None:
        dest = Destination(
            name="client-x",
            url="http://x:8000",
            api_key="k",
            include=("**/client-x/**",),
        )
        assert destination_is_active(dest, "/home/user/client-x/app")

    def test_pattern_does_not_match_different_path(self) -> None:
        dest = Destination(
            name="client-x",
            url="http://x:8000",
            api_key="k",
            include=("**/client-x/**",),
        )
        assert not destination_is_active(dest, "/home/user/client-y/app")

    def test_exclude_wins_over_include(self) -> None:
        """include=["**"] + exclude=["**/client-*/**"] → inactive for client paths (S3)."""
        dest = Destination(
            name="personal",
            url="http://p:8000",
            api_key="k",
            include=("**",),
            exclude=("**/client-*/**",),
        )
        assert not destination_is_active(dest, "/home/user/client-x/app"), (
            "exclude wins for client-x path"
        )
        assert destination_is_active(dest, "/home/user/personal-project"), (
            "non-client path should be active"
        )

    def test_no_include_patterns_inactive(self) -> None:
        """Empty include tuple → no match (impossible to reach include_matches)."""
        dest = Destination(name="x", url="http://x:8000", api_key="k", include=())
        assert not destination_is_active(dest, "/home/user/repos/app")


# ---------------------------------------------------------------------------
# Fan-out: select_active
# ---------------------------------------------------------------------------
class TestSelectActive:
    def _dest(self, name: str, include=("**",), exclude=()) -> Destination:
        return Destination(
            name=name, url=f"http://{name}:8000", api_key="k", include=include, exclude=exclude
        )

    def test_both_match_catchall(self) -> None:
        dests = {
            "personal": self._dest("personal"),
            "team": self._dest("team"),
        }
        active = select_active(dests, "/home/user/repos/app")
        assert set(active.keys()) == {"personal", "team"}

    def test_only_matching_destination_selected(self) -> None:
        dests = {
            "personal": self._dest("personal"),
            "team": self._dest("team", include=("**/client-x/**",)),
        }
        active = select_active(dests, "/home/user/personal-project")
        assert set(active.keys()) == {"personal"}

    def test_none_match_returns_empty(self) -> None:
        dests = {
            "client-x": self._dest("client-x", include=("**/client-x/**",)),
            "client-y": self._dest("client-y", include=("**/client-y/**",)),
        }
        active = select_active(dests, "/home/user/neutral-project")
        assert active == {}

    def test_fan_out_a_and_both(self) -> None:
        """A-only, A+B, neither — verifies correct subset in each case."""
        personal = self._dest(
            "personal",
            include=("**",),
            exclude=("**/client-*/**",),
        )
        team = self._dest("team", include=("**/client-x/**",))
        dests = {"personal": personal, "team": team}

        # personal only (no client in path)
        active = select_active(dests, "/home/user/personal-project")
        assert set(active.keys()) == {"personal"}

        # both (client-x path: team include matches, personal's exclude doesn't apply here)
        # personal: include=["**"] exclude=["**/client-*/**"] → excluded for client-x
        # team: include=["**/client-x/**"] → active
        active = select_active(dests, "/home/user/client-x/app")
        assert set(active.keys()) == {"team"}

        # neither (client-y path: team pattern doesn't match, personal excludes client paths)
        active = select_active(dests, "/home/user/client-y/app")
        assert active == {}

    def test_empty_destinations_returns_empty(self) -> None:
        assert select_active({}, "/home/user/repos/app") == {}


# ---------------------------------------------------------------------------
# Deep-merge depth test (C1 guard — replicate the proven scenario at data-shape level)
# ---------------------------------------------------------------------------
class TestDeepMergeConsumption:
    """Verify our consumption of a deep-merged destinations dict works correctly.

    The merge itself is app-cli's responsibility (already proven against real code).
    This test guards our Destination consumption of the merged shape.
    """

    def test_project_overrides_one_subkey_others_preserved(self) -> None:
        """Simulates: project scope overrode team.include; team.url/api_key must be preserved."""
        # After app-cli deep-merge, the config['destinations'] dict looks like:
        merged_destinations_config = {
            "personal": {
                "url": "http://personal:8000",
                "api_key": "pk",
                "include": ["**"],
                "exclude": ["**/client-*/**"],
            },
            "team": {
                "url": "http://team:8000",  # preserved from global
                "api_key": "team-key",  # preserved from global
                "include": ["**/client-x/**", "**/client-z/**"],  # overridden by project
            },
        }

        from unittest.mock import MagicMock
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        coordinator = MagicMock()
        coordinator.config = {}
        coordinator.get_capability = MagicMock(return_value=None)
        r = ConfigResolver({"destinations": merged_destinations_config}, coordinator)
        dests = r.destinations

        assert dests["team"].url == "http://team:8000", "team.url should be preserved"
        assert dests["team"].api_key == "team-key", "team.api_key should be preserved"
        assert "**/client-x/**" in dests["team"].include, "team.include should be overridden"
        assert dests["personal"].url == "http://personal:8000", "personal should be untouched"


# ---------------------------------------------------------------------------
# Gitignore directory semantics at the boundary (regression for the project-root
# misroute: a session started with `cd client-x && amplifier`).
# ---------------------------------------------------------------------------
class TestGitignoreDirectorySemantics:
    """A directory pattern must match the directory ROOT and its subtree, exactly
    like .gitignore. The bug: with a bare (no trailing slash) key, ``**/x/`` and
    ``**/x/**`` missed the directory itself, so a session launched from the project
    root was misrouted (excluded dest stayed active / included dest stayed inactive).
    """

    def _dest(self, name, include=("**",), exclude=()):
        return Destination(
            name=name, url="http://x:8000", api_key="k", include=include, exclude=exclude
        )

    def test_directory_pattern_matches_root_and_subtree(self) -> None:
        # The fixed example: team include ["**/client-x/"] must match BOTH the
        # project root and any subdirectory session.
        team = self._dest("team", include=("**/client-x/",))
        assert destination_is_active(team, normalize_match_key("/home/u/client-x"))
        assert destination_is_active(team, normalize_match_key("/home/u/client-x/app"))
        assert not destination_is_active(team, normalize_match_key("/home/u/other"))

    def test_exclude_directory_pattern_wins_at_root(self) -> None:
        # The fixed example: personal exclude ["**/client-*/"] must suppress the
        # client project AT ITS ROOT (the cd-into-project case) and below it.
        personal = self._dest("personal", include=("**",), exclude=("**/client-*/",))
        assert not destination_is_active(personal, normalize_match_key("/home/u/client-x"))
        assert not destination_is_active(personal, normalize_match_key("/home/u/client-x/app"))
        assert destination_is_active(personal, normalize_match_key("/home/u/play"))

    def test_b2_exfiltration_scenario_fixed(self) -> None:
        # Full scenario from the PR's example, exercising the real key normalization.
        # `cd client-x && amplifier` MUST go to team (not personal).
        personal = self._dest("personal", include=("**",), exclude=("**/client-*/",))
        team = self._dest("team", include=("**/client-x/",))
        dests = {"personal": personal, "team": team}
        active_root = select_active(dests, normalize_match_key("/home/u/client-x"))
        assert set(active_root) == {"team"}, "client-x root must route to team only"
        active_sub = select_active(dests, normalize_match_key("/home/u/client-x/svc"))
        assert set(active_sub) == {"team"}, "client-x subdir must route to team only"

    def test_legacy_starstar_pattern_now_matches_root_too(self) -> None:
        # With the directory key, the PR's ORIGINAL pattern "**/client-x/**" now
        # matches the project root as well as its contents — i.e. the previously
        # misrouting pattern is now safe (matches root + subtree). Documents the
        # actual behavior so nobody re-introduces the bug by "fixing" this.
        d = self._dest("d", include=("**/client-x/**",))
        assert destination_is_active(d, normalize_match_key("/home/u/client-x"))
        assert destination_is_active(d, normalize_match_key("/home/u/client-x/app"))
        assert not destination_is_active(d, normalize_match_key("/home/u/other"))

    def test_prefix_collision_is_safe(self) -> None:
        # `client-*` must not match a sibling `client` directory.
        d = self._dest("d", include=("**/client-*/",))
        assert not destination_is_active(d, normalize_match_key("/home/u/client"))
