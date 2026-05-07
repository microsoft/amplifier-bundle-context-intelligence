"""Tests for the revised context-intelligence-design-facilitator agent.

Verifies that:
- meta.description 'Use this agent when' bullets reflect the revised focus
- Old folder reference (.amplifier/context-intelligence/) is gone
- New folder reference (.context-intelligence-investigation/queries/) is present
- Section 5 contains transition guidance pointing to /brainstorm and warning
  against suggesting /write-plan directly
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
AGENT_PATH = REPO_ROOT / "agents" / "context-intelligence-design-facilitator.md"


@pytest.fixture
def agent_text() -> str:
    return AGENT_PATH.read_text()


class TestMetaDescriptionBullets:
    def test_starting_new_design_session_bullet(self, agent_text: str) -> None:
        assert "Starting a new context intelligence design session" in agent_text

    def test_designing_investigation_techniques_bullet(self, agent_text: str) -> None:
        assert (
            "Designing investigation techniques, metrics, and navigation approaches" in agent_text
        )

    def test_deriving_domain_signals_bullet(self, agent_text: str) -> None:
        assert "Deriving and interpreting domain-specific signals" in agent_text

    def test_synthesizing_findings_bullet(self, agent_text: str) -> None:
        assert "Synthesizing investigation findings into a structured design package" in agent_text

    def test_structuring_design_md_bullet(self, agent_text: str) -> None:
        assert "Structuring design.md following the upload tool pattern" in agent_text

    def test_old_bullets_removed(self, agent_text: str) -> None:
        # The four old bullets must be replaced, not augmented
        assert (
            "Translating investigation findings into component shape recommendations"
            not in agent_text
        )
        assert (
            "Identifying what new events or relationships would make a runtime more observable"
            not in agent_text
        )
        assert (
            "Deciding between skill / context file / agent / recipe / CLI for a given need"
            not in agent_text
        )


class TestFolderConvention:
    def test_new_folder_in_section_4(self, agent_text: str) -> None:
        assert ".context-intelligence-investigation/queries/" in agent_text

    def test_old_folder_fully_removed(self, agent_text: str) -> None:
        assert ".amplifier/context-intelligence/" not in agent_text


class TestSection5TransitionGuidance:
    def test_brainstorm_suggested(self, agent_text: str) -> None:
        assert "/brainstorm" in agent_text

    def test_systems_design_alternative_mentioned(self, agent_text: str) -> None:
        assert "/systems-design" in agent_text or "systems-design" in agent_text

    def test_write_plan_warning_present(self, agent_text: str) -> None:
        # Must explicitly warn 'Do NOT' or 'Do not' suggest /write-plan
        assert "do not suggest /write-plan" in agent_text.lower()

    def test_design_package_complete_phrase(self, agent_text: str) -> None:
        assert "When the design package is complete" in agent_text
