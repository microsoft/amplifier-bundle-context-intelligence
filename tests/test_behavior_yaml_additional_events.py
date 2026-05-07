"""Tests for task-10: additional_events config in behaviors/context-intelligence.yaml.

Tests:
- additional_events key exists in hook config
- additional_events is a list with exactly 5 entries
- Each of the 5 delegate event names is present
- additional_events appears after dispatch_failure_threshold in the YAML text
- additional_events appears before the base_path comment in the YAML text
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
BEHAVIOR_YAML_PATH = REPO_ROOT / "behaviors" / "context-intelligence.yaml"

EXPECTED_EVENTS = [
    "delegate:agent_spawned",
    "delegate:agent_resumed",
    "delegate:agent_completed",
    "delegate:agent_cancelled",
    "delegate:error",
]


@pytest.fixture
def behavior_yaml_content() -> str:
    """Return the raw text of the behavior YAML file."""
    return BEHAVIOR_YAML_PATH.read_text()


@pytest.fixture
def parsed_hook_config(behavior_yaml_content: str) -> dict:
    """Return the parsed hook config section from the behavior YAML."""
    import yaml

    data = yaml.safe_load(behavior_yaml_content)
    hooks = data.get("hooks", [])
    assert hooks, "No hooks defined in behaviors/context-intelligence.yaml"
    hook = hooks[0]
    config = hook.get("config", {})
    return config


class TestAdditionalEventsExists:
    """The additional_events key must exist in the hook config."""

    def test_additional_events_key_exists(self, parsed_hook_config: dict) -> None:
        """additional_events must be present in the hook config."""
        assert "additional_events" in parsed_hook_config, (
            "additional_events key not found in hook config"
        )

    def test_additional_events_is_list(self, parsed_hook_config: dict) -> None:
        """additional_events must be a list."""
        events = parsed_hook_config.get("additional_events")
        assert isinstance(events, list), f"additional_events must be a list, got {type(events)}"

    def test_additional_events_has_exactly_5_entries(self, parsed_hook_config: dict) -> None:
        """additional_events must contain exactly 5 entries."""
        events = parsed_hook_config.get("additional_events", [])
        assert len(events) == 5, (
            f"additional_events must have exactly 5 entries, got {len(events)}: {events}"
        )


class TestAdditionalEventsContents:
    """Each of the 5 delegate event names must be present."""

    @pytest.mark.parametrize("event_name", EXPECTED_EVENTS)
    def test_event_present(self, event_name: str, parsed_hook_config: dict) -> None:
        """Each expected event name must appear in additional_events."""
        events = parsed_hook_config.get("additional_events", [])
        assert event_name in events, f"'{event_name}' not found in additional_events: {events}"

    def test_exact_event_list(self, parsed_hook_config: dict) -> None:
        """additional_events must contain exactly the 5 specified events."""
        events = parsed_hook_config.get("additional_events", [])
        assert events == EXPECTED_EVENTS, (
            f"additional_events does not match expected.\n"
            f"Expected: {EXPECTED_EVENTS}\n"
            f"Got: {events}"
        )


class TestAdditionalEventsPosition:
    """additional_events must appear in the correct position in the YAML text."""

    def test_after_dispatch_failure_threshold(self, behavior_yaml_content: str) -> None:
        """additional_events must appear after dispatch_failure_threshold in the file."""
        pos_dispatch = behavior_yaml_content.find("dispatch_failure_threshold")
        pos_additional = behavior_yaml_content.find("additional_events")
        assert pos_dispatch != -1, "dispatch_failure_threshold not found in YAML"
        assert pos_additional != -1, "additional_events not found in YAML"
        assert pos_additional > pos_dispatch, (
            "additional_events must appear AFTER dispatch_failure_threshold"
        )

    def test_before_base_path_comment(self, behavior_yaml_content: str) -> None:
        """additional_events must appear before the base_path comment in the file."""
        pos_additional = behavior_yaml_content.find("additional_events")
        pos_base_path = behavior_yaml_content.find("# base_path:")
        assert pos_additional != -1, "additional_events not found in YAML"
        assert pos_base_path != -1, "# base_path: comment not found in YAML"
        assert pos_additional < pos_base_path, (
            "additional_events must appear BEFORE the # base_path: comment"
        )


class TestFacilitatorAgentRegistered:
    '''The context-intelligence-design-facilitator must be registered in
    behavior.agents.include so it is reachable via delegate.'''

    def test_facilitator_in_agents_include(self, behavior_yaml_content: str) -> None:
        import yaml

        data = yaml.safe_load(behavior_yaml_content)
        agents = data.get('agents', {})
        include = agents.get('include', [])
        assert 'context-intelligence:context-intelligence-design-facilitator' in include, (
            f'facilitator missing from agents.include: {include}'
        )
