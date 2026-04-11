"""Tests for skills/context-intelligence-session-reconstruction/SKILL.md (task-17).

Verifies:
1. File exists at the expected path
2. Valid YAML frontmatter (name, version, description, license)
3. Required content sections are present
4. Known limitations content matches spec
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent
SKILL_PATH = REPO_ROOT / "skills" / "context-intelligence-session-reconstruction" / "SKILL.md"


def _skill_content() -> str:
    return SKILL_PATH.read_text()


def _parse_frontmatter() -> dict:
    """Parse YAML frontmatter from the SKILL.md file."""
    content = _skill_content()
    if not content.startswith("---"):
        raise ValueError("SKILL.md does not start with YAML frontmatter (---)")
    # Find closing ---
    end = content.index("---", 3)
    frontmatter_text = content[3:end].strip()
    return yaml.safe_load(frontmatter_text)


class TestFileExists:
    """The file must exist at the expected path."""

    def test_skill_file_exists(self) -> None:
        assert SKILL_PATH.exists(), f"Expected {SKILL_PATH} to exist"

    def test_skill_file_is_not_empty(self) -> None:
        assert SKILL_PATH.stat().st_size > 0, f"Expected {SKILL_PATH} to be non-empty"


class TestFrontmatter:
    """Valid YAML frontmatter is required with correct field values."""

    def test_has_yaml_frontmatter(self) -> None:
        content = _skill_content()
        assert content.startswith("---"), "SKILL.md must start with YAML frontmatter (---)"

    def test_frontmatter_is_valid_yaml(self) -> None:
        fm = _parse_frontmatter()
        assert isinstance(fm, dict), "Frontmatter must be a valid YAML mapping"

    def test_name_field(self) -> None:
        fm = _parse_frontmatter()
        assert fm.get("name") == "context-intelligence-session-reconstruction"

    def test_version_field(self) -> None:
        fm = _parse_frontmatter()
        assert fm.get("version") == "1.0.0"

    def test_description_field_present(self) -> None:
        fm = _parse_frontmatter()
        assert "description" in fm, "Frontmatter must have a description field"
        assert fm["description"], "Description must not be empty"

    def test_description_mentions_reconstruction(self) -> None:
        fm = _parse_frontmatter()
        desc = fm.get("description", "").lower()
        assert "reconstruct" in desc or "session" in desc, (
            "Description must be about reconstructing session files"
        )

    def test_license_field(self) -> None:
        fm = _parse_frontmatter()
        assert fm.get("license") == "MIT"


class TestRequiredSections:
    """All required content sections must be present."""

    def test_has_when_to_use_section(self) -> None:
        assert "## When to Use" in _skill_content()

    def test_has_when_not_to_use_section(self) -> None:
        assert "## When NOT to Use" in _skill_content() or "## When Not to Use" in _skill_content()

    def test_has_prerequisites_section(self) -> None:
        assert "## Prerequisites" in _skill_content()

    def test_has_usage_patterns_section(self) -> None:
        assert "## Usage Patterns" in _skill_content()

    def test_has_verification_section(self) -> None:
        assert "## Verification" in _skill_content()

    def test_has_known_limitations_section(self) -> None:
        assert "## Known Limitations" in _skill_content()


class TestWhenToUseContent:
    """When to Use section must cover all specified use cases."""

    def test_mentions_missing_files(self) -> None:
        content = _skill_content().lower()
        assert "missing" in content and "file" in content

    def test_mentions_broken_resume(self) -> None:
        content = _skill_content().lower()
        assert "resume" in content

    def test_mentions_unknown_bundles(self) -> None:
        content = _skill_content().lower()
        assert "unknown" in content


class TestWhenNotToUseContent:
    """When NOT to Use section must cover all specified exclusions."""

    def test_mentions_server_unreachable(self) -> None:
        content = _skill_content().lower()
        assert "unreachable" in content or "not reachable" in content

    def test_mentions_pre_hook_sessions(self) -> None:
        content = _skill_content().lower()
        assert "pre-hook" in content or "prehook" in content or "pre hook" in content

    def test_mentions_real_time_data(self) -> None:
        content = _skill_content().lower()
        assert "real-time" in content or "real time" in content or "realtime" in content


class TestPrerequisitesContent:
    """Prerequisites section must cover graph server and API key configuration."""

    def test_mentions_graph_server_reachable(self) -> None:
        content = _skill_content().lower()
        assert "graph server" in content or "server" in content

    def test_mentions_status_check_command(self) -> None:
        content = _skill_content()
        assert "status" in content.lower()
        # The status subcommand
        assert "status" in content

    def test_mentions_api_key(self) -> None:
        content = _skill_content().lower()
        assert "api key" in content or "api_key" in content

    def test_mentions_env_var_method(self) -> None:
        content = _skill_content()
        # Environment variable method
        assert "AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY" in content or "env" in content.lower()

    def test_mentions_settings_yaml_method(self) -> None:
        content = _skill_content()
        assert "settings.yaml" in content

    def test_mentions_cli_flag_method(self) -> None:
        content = _skill_content()
        assert "--api-key" in content


class TestUsagePatternsContent:
    """Usage Patterns section must cover all specified patterns."""

    def test_mentions_metadata_only(self) -> None:
        assert "--metadata-only" in _skill_content()

    def test_mentions_dry_run(self) -> None:
        assert "--dry-run" in _skill_content()

    def test_mentions_verbose(self) -> None:
        assert "--verbose" in _skill_content()

    def test_mentions_session_flag(self) -> None:
        assert "--session" in _skill_content()

    def test_mentions_force(self) -> None:
        assert "--force" in _skill_content()

    def test_mentions_resolve_blobs(self) -> None:
        assert "--resolve-blobs" in _skill_content()

    def test_mentions_resolve_blobs_size(self) -> None:
        # Spec says ~19MB for raw LLM data
        assert "19" in _skill_content() or "MB" in _skill_content()


class TestVerificationContent:
    """Verification section must cover specified verification commands."""

    def test_mentions_wc_l_events(self) -> None:
        content = _skill_content()
        assert "wc -l" in content or "wc" in content

    def test_mentions_events_jsonl(self) -> None:
        assert "events.jsonl" in _skill_content()

    def test_mentions_transcript_jsonl(self) -> None:
        assert "transcript.jsonl" in _skill_content()

    def test_mentions_metadata_json(self) -> None:
        assert "metadata.json" in _skill_content()

    def test_mentions_amplifier_session_list(self) -> None:
        content = _skill_content().lower()
        assert "amplifier" in content and "session" in content and "list" in content


class TestKnownLimitationsContent:
    """Known Limitations section must cover all specified limitations."""

    def test_mentions_streaming_events_not_recoverable(self) -> None:
        content = _skill_content().lower()
        assert "streaming" in content or "content_block" in content

    def test_mentions_39_percent(self) -> None:
        assert "39" in _skill_content()

    def test_mentions_delegate_events_incomplete(self) -> None:
        content = _skill_content().lower()
        assert "delegate" in content and ("incomplete" in content or "partial" in content)

    def test_mentions_session_names_approximations(self) -> None:
        content = _skill_content().lower()
        assert "approximation" in content

    def test_mentions_pre_hook_no_graph_data(self) -> None:
        content = _skill_content().lower()
        assert "pre-hook" in content or "prehook" in content or "pre hook" in content
        assert "no graph" in content or "graph data" in content or "graph" in content
