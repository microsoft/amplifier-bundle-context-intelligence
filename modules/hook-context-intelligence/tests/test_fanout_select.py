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
    def test_absolute_path_returned_as_posix(self) -> None:
        result = normalize_match_key("/home/user/repos/app")
        assert result == "/home/user/repos/app"

    def test_tilde_expanded(self) -> None:
        result = normalize_match_key("~/repos/app")
        assert "~" not in result

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

    def test_star_star_name_slash_star_star_misses_directory_itself(self) -> None:
        """Gotcha: **/name/** matches only INSIDE the dir, not the dir itself.

        When the user runs `cd client-x && amplifier`, working_dir = .../client-x.
        The pattern **/client-x/** does NOT match that path — the dir is not inside itself.
        Documented in behaviors/context-intelligence.yaml and the design docs.
        """
        dest = Destination(
            name="team",
            url="http://x:8000",
            api_key="k",
            include=("**/client-x/**",),
        )
        # Inside the dir — should match
        assert destination_is_active(dest, "/home/user/client-x/app"), (
            "**/client-x/** must match /home/user/client-x/app (inside the dir)"
        )
        # The dir itself — must NOT match with just **/client-x/**
        assert not destination_is_active(dest, "/home/user/client-x"), (
            "**/client-x/** must NOT match /home/user/client-x (the dir itself)"
        )

    def test_dir_and_contents_idiom_matches_both(self) -> None:
        """Fix: include both **/name and **/name/** to match the dir itself AND its contents.

        This is the recommended pattern in the configuration examples.
        """
        dest = Destination(
            name="team",
            url="http://x:8000",
            api_key="k",
            include=("**/client-x", "**/client-x/**"),
        )
        # The dir itself — must match
        assert destination_is_active(dest, "/home/user/client-x"), (
            "**/client-x must match /home/user/client-x (the dir itself)"
        )
        # Inside the dir — must also match
        assert destination_is_active(dest, "/home/user/client-x/app"), (
            "**/client-x/** must match /home/user/client-x/app (inside the dir)"
        )
        # Different dir — must not match
        assert not destination_is_active(dest, "/home/user/client-y"), (
            "neither pattern should match client-y"
        )


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
