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
        """process_events([]) → {}."""
        from context_intelligence.bundle_analysis.processor import process_events

        assert process_events([]) == {}


class TestProcessEventsSchema:
    def test_schema_has_exactly_six_keys(self):
        """Each bundle entry must have exactly six keys."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [RawSignalEvent(kind="agent_spawned", agent="foundation:explorer")]
        result = process_events(events)
        assert set(result["foundation"].keys()) == {
            "agents",
            "skills",
            "recipes",
            "context",
            "modes",
            "tools",
        }

    def test_modes_always_zero(self):
        """modes is never incremented — always 0."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [RawSignalEvent(kind="agent_spawned", agent="foundation:explorer")]
        result = process_events(events)
        assert result["foundation"]["modes"] == 0

    def test_tools_always_zero(self):
        """tools is never incremented — always 0."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [RawSignalEvent(kind="agent_spawned", agent="foundation:explorer")]
        result = process_events(events)
        assert result["foundation"]["tools"] == 0


class TestProcessEventsAgentAttribution:
    def test_agent_spawned_increments_agents(self):
        """agent_spawned splits on ':' and increments agents for the bundle."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [RawSignalEvent(kind="agent_spawned", agent="foundation:explorer")]
        result = process_events(events)
        assert result["foundation"]["agents"] == 1

    def test_agent_spawned_multiple_for_same_bundle(self):
        """Multiple agent events for the same bundle accumulate."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [
            RawSignalEvent(kind="agent_spawned", agent="foundation:explorer"),
            RawSignalEvent(kind="agent_spawned", agent="foundation:zen-architect"),
        ]
        result = process_events(events)
        assert result["foundation"]["agents"] == 2

    def test_agents_from_different_bundles(self):
        """Agents from different bundles are tracked separately."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [
            RawSignalEvent(kind="agent_spawned", agent="foundation:explorer"),
            RawSignalEvent(kind="agent_spawned", agent="superpowers:implementer"),
        ]
        result = process_events(events)
        assert result["foundation"]["agents"] == 1
        assert result["superpowers"]["agents"] == 1

    def test_agent_without_colon_is_skipped(self):
        """Agent strings without ':' are not attributed to any bundle."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [RawSignalEvent(kind="agent_spawned", agent="bare-agent")]
        result = process_events(events)
        assert result == {}

    def test_agent_none_is_skipped(self):
        """Event with agent=None produces no attribution."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [RawSignalEvent(kind="agent_spawned", agent=None)]
        result = process_events(events)
        assert result == {}


class TestProcessEventsSkillAttribution:
    def test_skill_loaded_via_source_path(self):
        """skill_loaded uses _bundle_from_source_path to attribute the bundle."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        source = "/home/user/.amplifier/cache/skills/superpowers-a6aca0133cf890bf/skills"
        events = [RawSignalEvent(kind="skill_loaded", skill_source=source)]
        result = process_events(events)
        assert result["superpowers"]["skills"] == 1

    def test_skill_with_unparseable_source_is_skipped(self):
        """skill_loaded events where source cannot be parsed are silently skipped."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [RawSignalEvent(kind="skill_loaded", skill_source="/tmp/random/path")]
        result = process_events(events)
        assert result == {}

    def test_skill_with_none_source_is_skipped(self):
        """skill_loaded events with skill_source=None are silently skipped."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [RawSignalEvent(kind="skill_loaded", skill_source=None)]
        result = process_events(events)
        assert result == {}


class TestProcessEventsRecipeAttribution:
    def test_recipe_execute_with_at_prefix(self):
        """recipe_execute uses _bundle_from_recipe_path to attribute the bundle."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [
            RawSignalEvent(kind="recipe_execute", recipe_path="@recipes:examples/code-review.yaml")
        ]
        result = process_events(events)
        assert result["recipes"]["recipes"] == 1

    def test_recipe_without_at_prefix_is_skipped(self):
        """recipe_execute events without @ prefix produce no attribution."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [RawSignalEvent(kind="recipe_execute", recipe_path="plain/path/recipe.yaml")]
        result = process_events(events)
        assert result == {}

    def test_recipe_with_none_path_is_skipped(self):
        """recipe_execute events with recipe_path=None are silently skipped."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [RawSignalEvent(kind="recipe_execute", recipe_path=None)]
        result = process_events(events)
        assert result == {}


class TestProcessEventsMentionsResolved:
    def test_mentions_resolved_counts_new_entries(self):
        """mentions_resolved increments context for resolutions with is_new=True."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        resolutions = [
            {
                "is_new": True,
                "resolved_path": "/root/.amplifier/cache/amplifier-foundation-c909465861f9d6ce/context/foo.md",
                "source_type": "bundle_context_decl",
            },
        ]
        events = [RawSignalEvent(kind="mentions_resolved", resolutions=resolutions)]
        result = process_events(events)
        assert result["amplifier-foundation"]["context"] == 1

    def test_mentions_resolved_skips_non_new_resolutions(self):
        """Resolutions with is_new=False are not counted."""
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
        result = process_events(events)
        assert result == {}

    def test_mentions_resolved_skips_missing_source_type_and_no_mention(self):
        """Resolutions without 'source_type' and without extractable bundle are skipped."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        resolutions = [
            {"is_new": True},  # no source_type, no mention, no resolved_path
        ]
        events = [RawSignalEvent(kind="mentions_resolved", resolutions=resolutions)]
        result = process_events(events)
        assert result == {}

    def test_mentions_resolved_bundle_namespace_from_mention(self):
        """When source_type='bundle_namespace', bundle is extracted from mention string."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        resolutions = [
            {
                "is_new": True,
                "mention": "foundation:context/foo.md",
                "source_type": "bundle_namespace",
            },
        ]
        events = [RawSignalEvent(kind="mentions_resolved", resolutions=resolutions)]
        result = process_events(events)
        assert result["foundation"]["context"] == 1

    def test_mentions_resolved_bundle_context_decl_from_resolved_path(self):
        """When source_type='bundle_context_decl', bundle is extracted from resolved_path."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        resolutions = [
            {
                "is_new": True,
                "resolved_path": "/root/.amplifier/cache/amplifier-foundation-c909465861f9d6ce/context/foo.md",
                "source_type": "bundle_context_decl",
            },
        ]
        events = [RawSignalEvent(kind="mentions_resolved", resolutions=resolutions)]
        result = process_events(events)
        assert result["amplifier-foundation"]["context"] == 1

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
        result = process_events(events)
        assert result == {}

    def test_mentions_resolved_live_payload_format(self):
        """Full live event payload format (namespace mentions, no 'bundle' key) is handled."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        resolutions = [
            {
                "content_hash": "a059611a",
                "is_new": True,
                "mention": "foundation:context/bundle-awareness.md",
                "resolved_path": "/root/.amplifier/cache/amplifier-foundation-c909465861f9d6ce/context/bundle-awareness.md",
                "source_type": "bundle_namespace",
            },
            {
                "content_hash": "b1234567",
                "is_new": True,
                "mention": "recipes:context/recipe-awareness.md",
                "resolved_path": "/root/.amplifier/cache/amplifier-bundle-recipes-2b1e350432fea9ba/context/recipe-awareness.md",
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
                "resolved_path": "/root/.amplifier/cache/amplifier-foundation-c909465861f9d6ce/context/shared/common-agent-base.md",
                "source_type": "bundle_context_decl",
            },
        ]
        events = [RawSignalEvent(kind="mentions_resolved", resolutions=resolutions)]
        result = process_events(events)
        assert result["foundation"]["context"] == 1  # bundle_namespace from mention
        assert result["recipes"]["context"] == 1    # bundle_namespace from mention
        assert result["amplifier-foundation"]["context"] == 1  # bundle_context_decl from resolved_path

    def test_mentions_resolved_skips_empty_bundle(self):
        """Resolutions with unparseable resolved_path (empty or missing) are skipped."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        resolutions = [
            {"is_new": True, "resolved_path": "/tmp/no/marker/here", "source_type": "bundle_context_decl"},
        ]
        events = [RawSignalEvent(kind="mentions_resolved", resolutions=resolutions)]
        result = process_events(events)
        assert result == {}

    def test_mentions_resolved_skips_none_resolved_path(self):
        """Resolutions with resolved_path=None (unparseable) are skipped."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        resolutions = [
            {"is_new": True, "resolved_path": None, "source_type": "bundle_context_decl"},
        ]
        events = [RawSignalEvent(kind="mentions_resolved", resolutions=resolutions)]
        result = process_events(events)
        assert result == {}

    def test_mentions_resolved_multiple_new_entries(self):
        """Multiple new resolutions for the same bundle accumulate in context."""
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
        result = process_events(events)
        assert result["amplifier-foundation"]["context"] == 2

    def test_mentions_resolved_empty_resolutions_list(self):
        """Empty resolutions list produces no attribution."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [RawSignalEvent(kind="mentions_resolved", resolutions=[])]
        result = process_events(events)
        assert result == {}

    def test_mentions_resolved_none_resolutions_is_skipped(self):
        """resolutions=None produces no attribution."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [RawSignalEvent(kind="mentions_resolved", resolutions=None)]
        result = process_events(events)
        assert result == {}


class TestProcessEventsMixedAggregation:
    def test_mixed_events_aggregate_per_bundle(self):
        """All four event kinds contribute to the same bundle entry correctly."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        skill_source = "/home/user/.amplifier/cache/skills/foundation-a6aca0133cf890bf/skills"
        events = [
            RawSignalEvent(kind="agent_spawned", agent="foundation:explorer"),
            RawSignalEvent(kind="skill_loaded", skill_source=skill_source),
            RawSignalEvent(kind="recipe_execute", recipe_path="@foundation:recipes/deploy.yaml"),
            RawSignalEvent(
                kind="mentions_resolved",
                resolutions=[
                    {
                        "is_new": True,
                        "resolved_path": "/root/.amplifier/cache/foundation-a6aca0133cf890bf/context/foo.md",
                        "source_type": "bundle_context_decl",
                    }
                ],
            ),
        ]
        result = process_events(events)
        assert result["foundation"]["agents"] == 1
        assert result["foundation"]["skills"] == 1
        assert result["foundation"]["recipes"] == 1
        assert result["foundation"]["context"] == 1
        assert result["foundation"]["modes"] == 0
        assert result["foundation"]["tools"] == 0

    def test_multiple_bundles_tracked_independently(self):
        """Different bundles in the same event stream have separate entries."""
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent
        from context_intelligence.bundle_analysis.processor import process_events

        events = [
            RawSignalEvent(kind="agent_spawned", agent="foundation:explorer"),
            RawSignalEvent(kind="agent_spawned", agent="foundation:zen-architect"),
            RawSignalEvent(kind="agent_spawned", agent="superpowers:implementer"),
        ]
        result = process_events(events)
        assert result["foundation"]["agents"] == 2
        assert result["superpowers"]["agents"] == 1
        # superpowers has all other counters at 0
        assert result["superpowers"]["skills"] == 0
        assert result["superpowers"]["recipes"] == 0
        assert result["superpowers"]["context"] == 0
