"""Tests for context_intelligence.bundle_analysis.processor.

processor.py must:
- _bundle_from_source_path: parse skill source paths to bundle names
- _bundle_from_recipe_path: parse @bundle:path mentions to bundle names
- process_events: aggregate RawSignalEvents into per-bundle usage counts
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Tests for _bundle_from_source_path
# ---------------------------------------------------------------------------


class TestBundleFromSourcePath:
    def test_cache_skills_standard_path(self):
        """cache/skills/<slug>-<16hex>/skills → bundle name without hash."""
        from context_intelligence.bundle_analysis.processor import _bundle_from_source_path

        source = "/home/user/.amplifier/cache/skills/superpowers-a6aca0133cf890bf/skills"
        assert _bundle_from_source_path(source) == "superpowers"

    def test_cache_skills_deeper_subpath(self):
        """Deeper sub-paths still extract the correct slug."""
        from context_intelligence.bundle_analysis.processor import _bundle_from_source_path

        source = (
            "/home/user/.amplifier/cache/skills/"
            "superpowers-a6aca0133cf890bf/skills/brainstorming/skill.md"
        )
        assert _bundle_from_source_path(source) == "superpowers"

    def test_non_skills_cache_path(self):
        """Non-skills cache paths (/cache/<slug>-<hex>/...) also resolve."""
        from context_intelligence.bundle_analysis.processor import _bundle_from_source_path

        source = "/home/user/.amplifier/cache/foundation-1234567890abcdef/context/foo.md"
        assert _bundle_from_source_path(source) == "foundation"

    def test_slug_without_hash_returned_as_is(self):
        """When the slug has no 16-hex suffix, it is returned unchanged."""
        from context_intelligence.bundle_analysis.processor import _bundle_from_source_path

        source = "/home/user/.amplifier/cache/skills/foundation/skills"
        assert _bundle_from_source_path(source) == "foundation"

    def test_short_hex_suffix_not_stripped(self):
        """Only exactly 16 lowercase hex chars are stripped; shorter suffixes are kept."""
        from context_intelligence.bundle_analysis.processor import _bundle_from_source_path

        # "abc123" is only 6 hex chars — must NOT be stripped
        source = "/home/user/.amplifier/cache/skills/mything-abc123/skills"
        assert _bundle_from_source_path(source) == "mything-abc123"

    def test_no_cache_marker_returns_none(self):
        """Paths without a cache marker return None."""
        from context_intelligence.bundle_analysis.processor import _bundle_from_source_path

        assert _bundle_from_source_path("/tmp/random/path") is None

    def test_empty_string_returns_none(self):
        """Empty string returns None."""
        from context_intelligence.bundle_analysis.processor import _bundle_from_source_path

        assert _bundle_from_source_path("") is None

    def test_long_multi_segment_slug_hash_stripped(self):
        """Multi-segment slugs with 16-hex suffix are correctly stripped."""
        from context_intelligence.bundle_analysis.processor import _bundle_from_source_path

        source = (
            "/home/user/.amplifier/cache/skills/"
            "amplifier-bundle-context-intelligence-ecd41f3e6fa67bd2/skills"
        )
        assert _bundle_from_source_path(source) == "amplifier-bundle-context-intelligence"


# ---------------------------------------------------------------------------
# Tests for _bundle_from_recipe_path
# ---------------------------------------------------------------------------


class TestBundleFromRecipePath:
    def test_standard_bundle_path_mention(self):
        """Standard @bundle:path → bundle name before colon."""
        from context_intelligence.bundle_analysis.processor import _bundle_from_recipe_path

        assert _bundle_from_recipe_path("@recipes:examples/code-review.yaml") == "recipes"

    def test_bundle_name_only_separator(self):
        """@bundle: with only separator → empty string as bundle name."""
        from context_intelligence.bundle_analysis.processor import _bundle_from_recipe_path

        # @foundation: → "foundation" (empty path component after colon is fine)
        assert _bundle_from_recipe_path("@foundation:") == "foundation"

    def test_no_at_prefix_returns_none(self):
        """Paths without @ prefix return None."""
        from context_intelligence.bundle_analysis.processor import _bundle_from_recipe_path

        assert _bundle_from_recipe_path("recipes:examples/code-review.yaml") is None

    def test_no_colon_returns_none(self):
        """Paths without colon separator return None."""
        from context_intelligence.bundle_analysis.processor import _bundle_from_recipe_path

        assert _bundle_from_recipe_path("@recipes-only-no-colon") is None

    def test_empty_string_returns_none(self):
        """Empty string returns None."""
        from context_intelligence.bundle_analysis.processor import _bundle_from_recipe_path

        assert _bundle_from_recipe_path("") is None

    def test_just_at_sign_returns_none(self):
        """Just '@' returns None (no colon)."""
        from context_intelligence.bundle_analysis.processor import _bundle_from_recipe_path

        assert _bundle_from_recipe_path("@") is None

    def test_complex_bundle_name(self):
        """Multi-segment bundle names before the colon are returned correctly."""
        from context_intelligence.bundle_analysis.processor import _bundle_from_recipe_path

        assert _bundle_from_recipe_path("@my-bundle:path/to/recipe.yaml") == "my-bundle"


# ---------------------------------------------------------------------------
# Tests for process_events
# ---------------------------------------------------------------------------


class TestProcessEventsEmpty:
    def test_empty_list_returns_empty_dict(self):
        """process_events([], {}) → {}."""
        from context_intelligence.bundle_analysis.processor import process_events

        assert process_events([], {}) == {}


class TestProcessEventsSchema:
    def test_schema_has_exactly_six_keys(self):
        """Each bundle entry must have exactly six keys, all sets."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [RawSignalEvent(kind="agent_spawned", agent="foundation:explorer")]
        result = process_events(events, {})
        assert set(result["foundation"].keys()) == {
            "agents",
            "skills",
            "recipes",
            "context",
            "modes",
            "tools",
        }
        for val in result["foundation"].values():
            assert isinstance(val, set)

    def test_tools_empty_when_no_inventory_match(self):
        """Tools stay empty when inventory has no match for the tool name."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [RawSignalEvent(kind="tool_call", tool_name="unknown-tool")]
        result = process_events(events, {})
        # no bundle attributed because unknown-tool not in inventory
        assert result == {}

    def test_modes_empty_when_no_inventory_match(self):
        """Modes stay empty when inventory has no match for the mode name."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [RawSignalEvent(kind="mode_activated", mode_name="unknown-mode")]
        result = process_events(events, {})
        assert result == {}


class TestProcessEventsAgentAttribution:
    def test_agent_spawned_increments_agents(self):
        """agent_spawned splits on ':' and adds component to agents set for the bundle."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [RawSignalEvent(kind="agent_spawned", agent="foundation:explorer")]
        result = process_events(events, {})
        assert "explorer" in result["foundation"]["agents"]

    def test_agent_spawned_multiple_for_same_bundle(self):
        """Multiple agent events for the same bundle accumulate in the set."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [
            RawSignalEvent(kind="agent_spawned", agent="foundation:explorer"),
            RawSignalEvent(kind="agent_spawned", agent="foundation:zen-architect"),
        ]
        result = process_events(events, {})
        assert len(result["foundation"]["agents"]) == 2

    def test_agents_from_different_bundles(self):
        """Agents from different bundles are tracked separately."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [
            RawSignalEvent(kind="agent_spawned", agent="foundation:explorer"),
            RawSignalEvent(kind="agent_spawned", agent="superpowers:implementer"),
        ]
        result = process_events(events, {})
        assert "explorer" in result["foundation"]["agents"]
        assert "implementer" in result["superpowers"]["agents"]

    def test_agent_without_colon_is_skipped(self):
        """Agent strings without ':' are not attributed to any bundle."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [RawSignalEvent(kind="agent_spawned", agent="bare-agent")]
        result = process_events(events, {})
        assert result == {}

    def test_agent_none_is_skipped(self):
        """Event with agent=None produces no attribution."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [RawSignalEvent(kind="agent_spawned", agent=None)]
        result = process_events(events, {})
        assert result == {}


class TestProcessEventsSkillAttribution:
    def test_skill_loaded_via_source_path(self):
        """skill_loaded uses _bundle_from_source_path to attribute the bundle
        and Path(source).parent.name as the skill name."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        source = (
            "/home/user/.amplifier/cache/skills/"
            "superpowers-a6aca0133cf890bf/skills/brainstorming/SKILL.md"
        )
        events = [RawSignalEvent(kind="skill_loaded", skill_source=source)]
        result = process_events(events, {})
        assert "brainstorming" in result["superpowers"]["skills"]

    def test_skill_with_unparseable_source_is_skipped(self):
        """skill_loaded events where source cannot be parsed are silently skipped."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [RawSignalEvent(kind="skill_loaded", skill_source="/tmp/random/path")]
        result = process_events(events, {})
        assert result == {}

    def test_skill_with_none_source_is_skipped(self):
        """skill_loaded events with skill_source=None are silently skipped."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [RawSignalEvent(kind="skill_loaded", skill_source=None)]
        result = process_events(events, {})
        assert result == {}


class TestProcessEventsRecipeAttribution:
    def test_recipe_execute_with_at_prefix(self):
        """recipe_execute adds the path-after-colon to the recipes set for the bundle."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [
            RawSignalEvent(kind="recipe_execute", recipe_path="@recipes:examples/code-review.yaml")
        ]
        result = process_events(events, {})
        assert "examples/code-review.yaml" in result["recipes"]["recipes"]

    def test_recipe_without_at_prefix_is_skipped(self):
        """recipe_execute events without @ prefix produce no attribution."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [RawSignalEvent(kind="recipe_execute", recipe_path="plain/path/recipe.yaml")]
        result = process_events(events, {})
        assert result == {}

    def test_recipe_with_none_path_is_skipped(self):
        """recipe_execute events with recipe_path=None are silently skipped."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [RawSignalEvent(kind="recipe_execute", recipe_path=None)]
        result = process_events(events, {})
        assert result == {}


class TestProcessEventsMentionsResolved:
    def test_mentions_resolved_counts_new_entries(self):
        """mentions_resolved records resolved_path in context set for is_new=True resolutions."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        _rpath = "/root/.amplifier/cache/amplifier-foundation-c909465861f9d6ce/context/foo.md"
        resolutions = [
            {
                "is_new": True,
                "resolved_path": _rpath,
                "source_type": "bundle_context_decl",
            },
        ]
        events = [RawSignalEvent(kind="mentions_resolved", resolutions=resolutions)]
        result = process_events(events, {})
        assert _rpath in result["amplifier-foundation"]["context"]

    def test_mentions_resolved_skips_non_new_resolutions(self):
        """Resolutions with is_new=False are not recorded."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        resolutions = [
            {
                "is_new": False,
                "resolved_path": "/root/.amplifier/cache/amplifier-foundation-c909465861f9d6ce/context/foo.md",
                "source_type": "bundle_context_decl",
            },
        ]
        events = [RawSignalEvent(kind="mentions_resolved", resolutions=resolutions)]
        result = process_events(events, {})
        assert result == {}

    def test_mentions_resolved_skips_missing_source_type_and_no_mention(self):
        """Resolutions without 'source_type' and without extractable bundle are skipped."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        resolutions = [
            {"is_new": True},  # no source_type, no mention, no resolved_path
        ]
        events = [RawSignalEvent(kind="mentions_resolved", resolutions=resolutions)]
        result = process_events(events, {})
        assert result == {}

    def test_mentions_resolved_bundle_namespace_from_mention(self):
        """When source_type='bundle_namespace', bundle is extracted from mention string."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        _rpath = "/root/.amplifier/cache/amplifier-foundation-c909465861f9d6ce/context/foo.md"
        resolutions = [
            {
                "is_new": True,
                "mention": "foundation:context/foo.md",
                "resolved_path": _rpath,
                "source_type": "bundle_namespace",
            },
        ]
        events = [RawSignalEvent(kind="mentions_resolved", resolutions=resolutions)]
        result = process_events(events, {})
        assert _rpath in result["foundation"]["context"]

    def test_mentions_resolved_bundle_context_decl_from_resolved_path(self):
        """When source_type='bundle_context_decl', bundle is extracted from resolved_path."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        _rpath = "/root/.amplifier/cache/amplifier-foundation-c909465861f9d6ce/context/foo.md"
        resolutions = [
            {
                "is_new": True,
                "resolved_path": _rpath,
                "source_type": "bundle_context_decl",
            },
        ]
        events = [RawSignalEvent(kind="mentions_resolved", resolutions=resolutions)]
        result = process_events(events, {})
        assert _rpath in result["amplifier-foundation"]["context"]

    def test_mentions_resolved_skips_non_bundle_source_types(self):
        """Non-bundle source types (user_shortcut, home_shortcut, etc.) yield no attribution."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        resolutions = [
            {
                "is_new": True,
                "mention": "some:mention",
                "resolved_path": "/home/user/path",
                "source_type": "user_shortcut",
            },
            {
                "is_new": True,
                "mention": "another:mention",
                "resolved_path": "/home/path",
                "source_type": "home_shortcut",
            },
            {
                "is_new": True,
                "mention": "relative:mention",
                "resolved_path": "./relative/path",
                "source_type": "relative_path",
            },
            {
                "is_new": True,
                "mention": "project:mention",
                "resolved_path": "/project/path",
                "source_type": "project_shortcut",
            },
        ]
        events = [RawSignalEvent(kind="mentions_resolved", resolutions=resolutions)]
        result = process_events(events, {})
        assert result == {}

    def test_mentions_resolved_live_payload_format(self):
        """Full live event payload format (namespace mentions, no 'bundle' key) is handled."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        _rpath_foundation = "/root/.amplifier/cache/amplifier-foundation-c909465861f9d6ce/context/bundle-awareness.md"
        _rpath_recipes = "/root/.amplifier/cache/amplifier-bundle-recipes-2b1e350432fea9ba/context/recipe-awareness.md"
        _rpath_shared = "/root/.amplifier/cache/amplifier-foundation-c909465861f9d6ce/context/shared/common-agent-base.md"
        resolutions = [
            {
                "content_hash": "a059611a",
                "is_new": True,
                "mention": "foundation:context/bundle-awareness.md",
                "resolved_path": _rpath_foundation,
                "source_type": "bundle_namespace",
            },
            {
                "content_hash": "b1234567",
                "is_new": True,
                "mention": "recipes:context/recipe-awareness.md",
                "resolved_path": _rpath_recipes,
                "source_type": "bundle_namespace",
            },
            {
                "content_hash": "c9999999",
                "is_new": False,  # skipped
                "mention": "foundation:context/other.md",
                "resolved_path": "/root/.amplifier/cache/amplifier-foundation-c909465861f9d6ce/context/other.md",
                "source_type": "bundle_namespace",
            },
            {
                "content_hash": "d0000000",
                "is_new": True,
                "mention": None,  # bundle_context_decl — no mention string
                "resolved_path": _rpath_shared,
                "source_type": "bundle_context_decl",
            },
        ]
        events = [RawSignalEvent(kind="mentions_resolved", resolutions=resolutions)]
        result = process_events(events, {})
        assert _rpath_foundation in result["foundation"]["context"]  # bundle_namespace from mention
        assert _rpath_recipes in result["recipes"]["context"]  # bundle_namespace from mention
        assert _rpath_shared in result["amplifier-foundation"]["context"]  # bundle_context_decl

    def test_mentions_resolved_skips_empty_bundle(self):
        """Resolutions with unparseable resolved_path (empty or missing) are skipped."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        resolutions = [
            {
                "is_new": True,
                "resolved_path": "/tmp/no/marker/here",
                "source_type": "bundle_context_decl",
            },
        ]
        events = [RawSignalEvent(kind="mentions_resolved", resolutions=resolutions)]
        result = process_events(events, {})
        assert result == {}

    def test_mentions_resolved_skips_none_resolved_path(self):
        """Resolutions with resolved_path=None (unparseable) are skipped."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        resolutions = [
            {"is_new": True, "resolved_path": None, "source_type": "bundle_context_decl"},
        ]
        events = [RawSignalEvent(kind="mentions_resolved", resolutions=resolutions)]
        result = process_events(events, {})
        assert result == {}

    def test_mentions_resolved_multiple_new_entries(self):
        """Multiple new resolutions for the same bundle accumulate in context set."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        resolutions = [
            {
                "is_new": True,
                "resolved_path": "/root/.amplifier/cache/amplifier-foundation-c909465861f9d6ce/context/foo.md",
                "source_type": "bundle_context_decl",
            },
            {
                "is_new": True,
                "resolved_path": "/root/.amplifier/cache/amplifier-foundation-c909465861f9d6ce/context/bar.md",
                "source_type": "bundle_context_decl",
            },
            {
                "is_new": False,
                "resolved_path": "/root/.amplifier/cache/amplifier-foundation-c909465861f9d6ce/context/other.md",
                "source_type": "bundle_context_decl",
            },  # skipped
        ]
        events = [RawSignalEvent(kind="mentions_resolved", resolutions=resolutions)]
        result = process_events(events, {})
        assert len(result["amplifier-foundation"]["context"]) == 2

    def test_mentions_resolved_empty_resolutions_list(self):
        """Empty resolutions list produces no attribution."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [RawSignalEvent(kind="mentions_resolved", resolutions=[])]
        result = process_events(events, {})
        assert result == {}

    def test_mentions_resolved_none_resolutions_is_skipped(self):
        """resolutions=None produces no attribution."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [RawSignalEvent(kind="mentions_resolved", resolutions=None)]
        result = process_events(events, {})
        assert result == {}


class TestProcessEventsMixedAggregation:
    def test_mixed_events_aggregate_per_bundle(self):
        """All four event kinds contribute to the same bundle entry correctly."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        skill_source = (
            "/home/user/.amplifier/cache/skills/"
            "foundation-a6aca0133cf890bf/skills/code-review/skill.md"
        )
        _rpath = "/root/.amplifier/cache/foundation-a6aca0133cf890bf/context/foo.md"
        events = [
            RawSignalEvent(kind="agent_spawned", agent="foundation:explorer"),
            RawSignalEvent(kind="skill_loaded", skill_source=skill_source),
            RawSignalEvent(kind="recipe_execute", recipe_path="@foundation:recipes/deploy.yaml"),
            RawSignalEvent(
                kind="mentions_resolved",
                resolutions=[
                    {
                        "is_new": True,
                        "resolved_path": _rpath,
                        "source_type": "bundle_context_decl",
                    }
                ],
            ),
        ]
        result = process_events(events, {})
        assert "explorer" in result["foundation"]["agents"]
        assert "code-review" in result["foundation"]["skills"]
        assert "recipes/deploy.yaml" in result["foundation"]["recipes"]
        assert _rpath in result["foundation"]["context"]
        assert result["foundation"]["modes"] == set()
        assert result["foundation"]["tools"] == set()

    def test_multiple_bundles_tracked_independently(self):
        """Different bundles in the same event stream have separate entries."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [
            RawSignalEvent(kind="agent_spawned", agent="foundation:explorer"),
            RawSignalEvent(kind="agent_spawned", agent="foundation:zen-architect"),
            RawSignalEvent(kind="agent_spawned", agent="superpowers:implementer"),
        ]
        result = process_events(events, {})
        assert len(result["foundation"]["agents"]) == 2
        assert len(result["superpowers"]["agents"]) == 1
        # superpowers has all other sets empty
        assert result["superpowers"]["skills"] == set()
        assert result["superpowers"]["recipes"] == set()
        assert result["superpowers"]["context"] == set()


# ---------------------------------------------------------------------------
# NEW: TestProcessEventsNamedSets — verifies the v2 API (named sets, inventory)
# ---------------------------------------------------------------------------


class TestProcessEventsNamedSets:
    def test_signature_takes_inventory(self):
        """process_events must accept 'events' and 'inventory' positional args."""
        import inspect

        from context_intelligence.bundle_analysis.processor import process_events

        sig = inspect.signature(process_events)
        assert list(sig.parameters.keys()) == ["events", "inventory"]

    def test_returns_empty_dict_for_no_events(self):
        """process_events([], {}) returns {}."""
        from context_intelligence.bundle_analysis.processor import process_events

        assert process_events([], {}) == {}

    def test_agent_spawned_adds_named_agent_to_set(self):
        """agent_spawned adds the component name to the agents set."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [RawSignalEvent(kind="agent_spawned", agent="foundation:explorer")]
        result = process_events(events, {})
        assert result["foundation"]["agents"] == {"explorer"}

    def test_six_keys_per_bundle_all_sets(self):
        """Each bundle entry has exactly six keys and every value is a set."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import _SIGNAL_KEYS, process_events

        events = [RawSignalEvent(kind="agent_spawned", agent="foundation:explorer")]
        result = process_events(events, {})
        assert set(result["foundation"].keys()) == set(_SIGNAL_KEYS)
        for key in _SIGNAL_KEYS:
            assert isinstance(result["foundation"][key], set)

    def test_skill_loaded_extracts_skill_name_from_path(self):
        """skill_loaded adds the skill folder name (parent.name) to the skills set."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        source = (
            "/home/user/.amplifier/cache/skills/"
            "superpowers-a6aca0133cf890bf/skills/brainstorming/SKILL.md"
        )
        events = [RawSignalEvent(kind="skill_loaded", skill_source=source)]
        result = process_events(events, {})
        assert "brainstorming" in result["superpowers"]["skills"]

    def test_recipe_execute_extracts_recipe_name(self):
        """recipe_execute adds the path-after-colon to the recipes set."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [
            RawSignalEvent(
                kind="recipe_execute", recipe_path="@superpowers:recipes/brainstorm.yaml"
            )
        ]
        result = process_events(events, {})
        assert "recipes/brainstorm.yaml" in result["superpowers"]["recipes"]

    def test_mentions_resolved_records_resolved_path(self):
        """mentions_resolved with 'bundle' key adds resolved_path to the context set."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        resolutions = [{"bundle": "foundation", "resolved_path": "context/foo.md"}]
        events = [RawSignalEvent(kind="mentions_resolved", resolutions=resolutions)]
        result = process_events(events, {})
        assert "context/foo.md" in result["foundation"]["context"]


# ---------------------------------------------------------------------------
# TestBuildReverseLookups — verifies the three-tier inventory schema is read
# ---------------------------------------------------------------------------


class TestBuildReverseLookups:
    def test_always_active_agents_populate_agent_to_bundle(self):
        """Inventory with always_active.agents populates agent_to_bundle map."""
        from context_intelligence.bundle_analysis.processor import _build_reverse_lookups

        inventory = {
            "foundation": {
                "always_active": {
                    "agents": {"explorer", "zen-architect"},
                    "skills": set(),
                    "context": set(),
                    "recipes": set(),
                },
                "agent_level": {},
                "mode_gated": {},
                "modes": set(),
            }
        }
        agent_to_bundle, _skill, _tool, _mode = _build_reverse_lookups(inventory)
        assert agent_to_bundle.get("explorer") == "foundation"
        assert agent_to_bundle.get("zen-architect") == "foundation"

    def test_always_active_skills_populate_skill_to_bundle(self):
        """Inventory with always_active.skills populates skill_to_bundle map."""
        from context_intelligence.bundle_analysis.processor import _build_reverse_lookups

        inventory = {
            "superpowers": {
                "always_active": {
                    "agents": set(),
                    "skills": {"brainstorming", "code-review"},
                    "context": set(),
                    "recipes": set(),
                },
                "agent_level": {},
                "mode_gated": {},
                "modes": set(),
            }
        }
        _agent, skill_to_bundle, _tool, _mode = _build_reverse_lookups(inventory)
        assert skill_to_bundle.get("brainstorming") == "superpowers"
        assert skill_to_bundle.get("code-review") == "superpowers"

    def test_bare_agent_attributed_via_inventory_reverse_lookup(self):
        """agent_spawned with bare name (no ':') resolves bundle via always_active.agents."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        inventory = {
            "foundation": {
                "always_active": {
                    "agents": {"explorer"},
                    "skills": set(),
                    "context": set(),
                    "recipes": set(),
                },
                "agent_level": {},
                "mode_gated": {},
                "modes": set(),
            }
        }
        # Bare agent name — no ':' separator
        events = [RawSignalEvent(kind="agent_spawned", agent="explorer")]
        result = process_events(events, inventory)
        assert "foundation" in result
        assert "explorer" in result["foundation"]["agents"]

    def test_declared_key_no_longer_populates_lookups(self):
        """Old 'declared' key is ignored — only 'always_active' populates lookups."""
        from context_intelligence.bundle_analysis.processor import _build_reverse_lookups

        inventory = {
            "foundation": {
                "declared": {
                    "agents": ["explorer"],
                    "skills": ["brainstorming"],
                },
            }
        }
        agent_to_bundle, skill_to_bundle, _tool, _mode = _build_reverse_lookups(inventory)
        # The old 'declared' key must NOT populate the maps
        assert agent_to_bundle.get("explorer") is None
        assert skill_to_bundle.get("brainstorming") is None

    def test_mode_to_bundle_from_inventory_modes(self):
        from context_intelligence.bundle_analysis.processor import _build_reverse_lookups

        inventory = {
            "superpowers": {
                "always_active": {
                    "agents": set(),
                    "skills": set(),
                    "context": set(),
                    "recipes": set(),
                },
                "agent_level": {},
                "mode_gated": {},
                "modes": {"brainstorm", "write-plan", "execute-plan"},
            }
        }
        _, _, _, mode_to_bundle = _build_reverse_lookups(inventory)
        assert mode_to_bundle.get("brainstorm") == "superpowers"
        assert mode_to_bundle.get("write-plan") == "superpowers"

    def test_agent_level_tools_in_tool_to_bundle(self):
        from context_intelligence.bundle_analysis.processor import _build_reverse_lookups

        inventory = {
            "foundation": {
                "always_active": {
                    "agents": set(),
                    "skills": set(),
                    "context": set(),
                    "recipes": set(),
                },
                "agent_level": {
                    "explorer": {
                        "tools": {"tool-bash", "tool-read-file"},
                        "context": set(),
                        "skills": set(),
                    }
                },
                "mode_gated": {},
                "modes": set(),
            }
        }
        _, _, tool_to_bundle, _ = _build_reverse_lookups(inventory)
        assert tool_to_bundle.get("tool-bash") == "foundation"
        assert tool_to_bundle.get("tool-read-file") == "foundation"

    def test_dict_tool_module_normalized_to_event_name(self):
        """Dict tools from agent_level (real inventory format) are normalized:
        strip 'tool-' prefix and replace '-' with '_' to match event tool names.
        e.g. {'module': 'tool-graph-query'} -> 'graph_query' in tool_to_bundle.
        """
        from context_intelligence.bundle_analysis.processor import _build_reverse_lookups

        inventory = {
            "context-intelligence": {
                "always_active": {
                    "agents": set(),
                    "skills": set(),
                    "context": set(),
                    "recipes": set(),
                },
                "agent_level": {
                    "graph-analyst": {
                        "tools": [
                            {
                                "module": "tool-bash",
                                "source": "git+https://github.com/microsoft/amplifier-module-tool-bash@main",
                            },
                            {"module": "tool-graph-query", "source": "git+https://github.com/..."},
                            {"module": "tool-delegate", "source": "git+https://github.com/..."},
                        ],
                        "context": set(),
                        "skills": set(),
                    }
                },
                "mode_gated": {},
                "modes": set(),
            }
        }
        _, _, tool_to_bundle, _ = _build_reverse_lookups(inventory)
        # "tool-bash" -> "bash"
        assert tool_to_bundle.get("bash") == "context-intelligence"
        # "tool-graph-query" -> "graph_query"
        assert tool_to_bundle.get("graph_query") == "context-intelligence"
        # "tool-delegate" -> "delegate"
        assert tool_to_bundle.get("delegate") == "context-intelligence"

    def test_dict_tool_without_tool_prefix_skipped(self):
        """Dict tools where module name doesn't start with 'tool-' are skipped."""
        from context_intelligence.bundle_analysis.processor import _build_reverse_lookups

        inventory = {
            "some-bundle": {
                "always_active": {
                    "agents": set(),
                    "skills": set(),
                    "context": set(),
                    "recipes": set(),
                },
                "agent_level": {
                    "some-agent": {
                        "tools": [
                            {"module": "not-a-tool", "source": "git+https://..."},
                        ],
                        "context": set(),
                        "skills": set(),
                    }
                },
                "mode_gated": {},
                "modes": set(),
            }
        }
        _, _, tool_to_bundle, _ = _build_reverse_lookups(inventory)
        assert "not-a-tool" not in tool_to_bundle
        assert "a_tool" not in tool_to_bundle

    def test_dict_tool_process_events_with_real_inventory_format(self):
        """process_events attributes real event tool names when inventory has dict tools."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        inventory = {
            "context-intelligence": {
                "always_active": {
                    "agents": set(),
                    "skills": set(),
                    "context": set(),
                    "recipes": set(),
                },
                "agent_level": {
                    "graph-analyst": {
                        "tools": [
                            {"module": "tool-bash", "source": "..."},
                            {"module": "tool-graph-query", "source": "..."},
                        ],
                        "context": set(),
                        "skills": set(),
                    }
                },
                "mode_gated": {},
                "modes": set(),
            }
        }
        # Events use bare tool names (as seen in real events.jsonl)
        events = [
            RawSignalEvent(kind="tool_call", tool_name="bash"),
            RawSignalEvent(kind="tool_call", tool_name="graph_query"),
        ]
        result = process_events(events, inventory)
        assert "context-intelligence" in result
        assert "bash" in result["context-intelligence"]["tools"]
        assert "graph_query" in result["context-intelligence"]["tools"]


# ---------------------------------------------------------------------------
# TestProcessEventsToolAttribution
# ---------------------------------------------------------------------------


class TestProcessEventsToolAttribution:
    def test_tool_call_attributed_via_inventory(self):
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        inventory = {
            "foundation": {
                "always_active": {
                    "agents": set(),
                    "skills": set(),
                    "context": set(),
                    "recipes": set(),
                },
                "agent_level": {
                    "explorer": {"tools": {"tool-bash"}, "context": set(), "skills": set()}
                },
                "mode_gated": {},
                "modes": set(),
            }
        }
        events = [RawSignalEvent(kind="tool_call", tool_name="tool-bash")]
        result = process_events(events, inventory)
        assert "foundation" in result
        assert "tool-bash" in result["foundation"]["tools"]

    def test_unknown_tool_not_attributed(self):
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [RawSignalEvent(kind="tool_call", tool_name="unknown-tool")]
        result = process_events(
            events,
            {
                "foundation": {
                    "always_active": {
                        "agents": set(),
                        "skills": set(),
                        "context": set(),
                        "recipes": set(),
                    },
                    "agent_level": {},
                    "mode_gated": {},
                    "modes": set(),
                }
            },
        )
        assert "foundation" not in result or "unknown-tool" not in result.get("foundation", {}).get(
            "tools", set()
        )

    def test_tool_name_none_skipped(self):
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [RawSignalEvent(kind="tool_call", tool_name=None)]
        result = process_events(events, {})
        assert result == {}


# ---------------------------------------------------------------------------
# TestProcessEventsModeAttribution
# ---------------------------------------------------------------------------


class TestProcessEventsModeAttribution:
    def test_mode_activated_attributed_via_inventory(self):
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        inventory = {
            "superpowers": {
                "always_active": {
                    "agents": set(),
                    "skills": set(),
                    "context": set(),
                    "recipes": set(),
                },
                "agent_level": {},
                "mode_gated": {},
                "modes": {"brainstorm", "write-plan"},
            }
        }
        events = [RawSignalEvent(kind="mode_activated", mode_name="brainstorm")]
        result = process_events(events, inventory)
        assert "superpowers" in result
        assert "brainstorm" in result["superpowers"]["modes"]

    def test_unknown_mode_not_attributed(self):
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [RawSignalEvent(kind="mode_activated", mode_name="unknown-mode")]
        result = process_events(events, {})
        assert result == {}

    def test_mode_name_none_skipped(self):
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [RawSignalEvent(kind="mode_activated", mode_name=None)]
        result = process_events(events, {})
        assert result == {}
