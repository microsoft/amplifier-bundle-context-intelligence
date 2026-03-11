"""Tests for the session navigation skill (context-intelligence-session-navigation).

Validates that SKILL.md exists with correct structure, frontmatter,
disk layout, record format, metadata.json schema, safe extraction discipline,
event taxonomy, navigation patterns, and project slug algorithm.

Also validates companion context files: event-schema.md and safe-extraction-patterns.md.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

BUNDLE_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = BUNDLE_ROOT / "skills" / "context-intelligence-session-navigation"
SKILL_FILE = SKILL_DIR / "SKILL.md"
EVENT_SCHEMA_FILE = BUNDLE_ROOT / "context" / "event-schema.md"
EXTRACTION_PATTERNS_FILE = BUNDLE_ROOT / "context" / "safe-extraction-patterns.md"

# Read and parse once at module level — avoids redundant disk reads.
try:
    _SKILL_CONTENT: str = SKILL_FILE.read_text() if SKILL_FILE.exists() else ""
except OSError:
    _SKILL_CONTENT = ""

try:
    _EVENT_SCHEMA_CONTENT: str = (
        EVENT_SCHEMA_FILE.read_text() if EVENT_SCHEMA_FILE.exists() else ""
    )
except OSError:
    _EVENT_SCHEMA_CONTENT = ""

try:
    _EXTRACTION_CONTENT: str = (
        EXTRACTION_PATTERNS_FILE.read_text()
        if EXTRACTION_PATTERNS_FILE.exists()
        else ""
    )
except OSError:
    _EXTRACTION_CONTENT = ""


def _parse_frontmatter(content: str) -> dict:
    """Extract YAML frontmatter from markdown content."""
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return {}
    return yaml.safe_load(match.group(1))


_FRONTMATTER: dict = _parse_frontmatter(_SKILL_CONTENT) if _SKILL_CONTENT else {}


def _extract_section(content: str, heading: str, level: str = "##") -> str:
    """Extract content under a markdown heading, up to the next heading of same level or end.

    Example: _extract_section(text, "Foo", "##") returns everything after
    '## Foo\\n' up to the next '## Bar' heading or end of file.
    """
    escaped = re.escape(heading)
    escaped_level = re.escape(level)
    pattern = rf"^{escaped_level} {escaped}\s*\n(.*?)(?=^{escaped_level} |\Z)"
    match = re.search(pattern, content, re.MULTILINE | re.DOTALL)
    if not match:
        return ""
    return match.group(1)


# Pre-extract SKILL.md sections.
_DISK_LAYOUT_SECTION: str = (
    _extract_section(_SKILL_CONTENT, "Disk Layout") if _SKILL_CONTENT else ""
)
_RECORD_FORMAT_SECTION: str = (
    _extract_section(_SKILL_CONTENT, "Record Format") if _SKILL_CONTENT else ""
)
_METADATA_SECTION: str = (
    _extract_section(_SKILL_CONTENT, "metadata.json") if _SKILL_CONTENT else ""
)
_SAFE_EXTRACTION_SECTION: str = (
    _extract_section(_SKILL_CONTENT, "Safe Extraction Discipline")
    if _SKILL_CONTENT
    else ""
)
_EVENT_TAXONOMY_SECTION: str = (
    _extract_section(_SKILL_CONTENT, "Event Name Taxonomy") if _SKILL_CONTENT else ""
)
_NAVIGATION_SECTION: str = (
    _extract_section(_SKILL_CONTENT, "Common Navigation Patterns")
    if _SKILL_CONTENT
    else ""
)
_SLUG_SECTION: str = (
    _extract_section(_SKILL_CONTENT, "Project Slug Algorithm") if _SKILL_CONTENT else ""
)


# ——————————————————————————————————————————————————————
# SKILL.md — File and directory existence
# ——————————————————————————————————————————————————————


def test_skill_file_exists() -> None:
    assert SKILL_FILE.exists(), f"SKILL.md not found at {SKILL_FILE}"


def test_skill_directory_exists() -> None:
    assert SKILL_DIR.is_dir(), f"Skill directory not found at {SKILL_DIR}"


# ——————————————————————————————————————————————————————
# SKILL.md — YAML frontmatter
# ——————————————————————————————————————————————————————


def test_frontmatter_name() -> None:
    assert _FRONTMATTER.get("name") == "context-intelligence-session-navigation"


def test_frontmatter_description() -> None:
    desc = _FRONTMATTER.get("description", "")
    assert len(desc) > 0, "description must be non-empty"
    assert "JSONL" in desc, "description should mention JSONL"


def test_frontmatter_version() -> None:
    assert _FRONTMATTER.get("version") == "0.1.0"


def test_frontmatter_license() -> None:
    assert _FRONTMATTER.get("license") == "MIT"


# ——————————————————————————————————————————————————————
# SKILL.md — Disk Layout section
# ——————————————————————————————————————————————————————


def test_disk_layout_section_exists() -> None:
    assert "## Disk Layout" in _SKILL_CONTENT, (
        "SKILL.md should have a ## Disk Layout section"
    )


def test_disk_layout_mentions_projects_path() -> None:
    assert ".amplifier/projects" in _DISK_LAYOUT_SECTION, (
        "Disk Layout should reference ~/.amplifier/projects"
    )


def test_disk_layout_mentions_sessions_directory() -> None:
    assert "sessions" in _DISK_LAYOUT_SECTION, (
        "Disk Layout should reference sessions directory"
    )


def test_disk_layout_mentions_events_jsonl() -> None:
    assert "events.jsonl" in _DISK_LAYOUT_SECTION, (
        "Disk Layout should reference events.jsonl"
    )


def test_disk_layout_mentions_metadata_json() -> None:
    assert "metadata.json" in _DISK_LAYOUT_SECTION, (
        "Disk Layout should reference metadata.json"
    )


# ——————————————————————————————————————————————————————
# SKILL.md — Record Format section
# ——————————————————————————————————————————————————————


def test_record_format_section_exists() -> None:
    assert "## Record Format" in _SKILL_CONTENT, (
        "SKILL.md should have a ## Record Format section"
    )


def test_record_format_three_fields() -> None:
    """Record format documents the three always-present fields."""
    for field in ["event", "timestamp", "data"]:
        assert field in _RECORD_FORMAT_SECTION, (
            f"Record Format should document '{field}' field"
        )


def test_record_format_no_mutation_rule() -> None:
    """Record format states no field promotion/classification/mutation."""
    assert re.search(
        r"no field promotion|no.*mutation|no.*classification",
        _RECORD_FORMAT_SECTION,
        re.IGNORECASE,
    ), "Record Format should state no field promotion/classification/mutation"


# ——————————————————————————————————————————————————————
# SKILL.md — metadata.json section
# ——————————————————————————————————————————————————————


def test_metadata_section_exists() -> None:
    assert "## metadata.json" in _SKILL_CONTENT, (
        "SKILL.md should have a ## metadata.json section"
    )


def test_metadata_required_fields() -> None:
    """Required fields table has all 5 required fields."""
    for field in ["session_id", "parent_id", "started_at", "status", "working_dir"]:
        assert field in _METADATA_SECTION, (
            f"metadata.json section should document required field '{field}'"
        )


def test_metadata_optional_fields() -> None:
    """Optional fields table has all 5 optional fields."""
    for field in [
        "agent_name",
        "parallel_group_id",
        "recipe_name",
        "recipe_step",
        "ended_at",
    ]:
        assert field in _METADATA_SECTION, (
            f"metadata.json section should document optional field '{field}'"
        )


def test_metadata_no_nulls_rule() -> None:
    """Documents the 'omitted when absent, no nulls' rule."""
    assert re.search(r"no null", _METADATA_SECTION, re.IGNORECASE), (
        "metadata.json section should state no nulls rule"
    )


# ——————————————————————————————————————————————————————
# SKILL.md — Safe Extraction Discipline section
# ——————————————————————————————————————————————————————


def test_safe_extraction_section_exists() -> None:
    assert "## Safe Extraction Discipline" in _SKILL_CONTENT, (
        "SKILL.md should have a ## Safe Extraction Discipline section"
    )


def test_safe_extraction_never_cat_rule() -> None:
    assert re.search(
        r"never.*cat|do not.*cat", _SAFE_EXTRACTION_SECTION, re.IGNORECASE
    ), "Safe Extraction should include 'never cat' rule"


def test_safe_extraction_jq_rule() -> None:
    assert "jq -c" in _SAFE_EXTRACTION_SECTION, (
        "Safe Extraction should include 'jq -c' rule"
    )


def test_safe_extraction_grep_cut_rule() -> None:
    assert "grep" in _SAFE_EXTRACTION_SECTION and "cut" in _SAFE_EXTRACTION_SECTION, (
        "Safe Extraction should include 'grep -n | cut' rule"
    )


def test_safe_extraction_preview_rule() -> None:
    assert "wc -l" in _SAFE_EXTRACTION_SECTION and "head" in _SAFE_EXTRACTION_SECTION, (
        "Safe Extraction should include 'wc -l and head' preview rule"
    )


# ——————————————————————————————————————————————————————
# SKILL.md — Event Name Taxonomy section
# ——————————————————————————————————————————————————————


def test_event_taxonomy_section_exists() -> None:
    assert "## Event Name Taxonomy" in _SKILL_CONTENT, (
        "SKILL.md should have an ## Event Name Taxonomy section"
    )


def test_event_taxonomy_namespace_action_convention() -> None:
    assert "{namespace}:{action}" in _EVENT_TAXONOMY_SECTION, (
        "Event Taxonomy should document {namespace}:{action} convention"
    )


def test_event_taxonomy_covers_all_namespaces() -> None:
    """Event taxonomy table covers all 10 canonical namespaces."""
    for ns in [
        "session",
        "prompt",
        "provider",
        "llm",
        "tool",
        "orchestrator",
        "context",
        "cancel",
        "recipe",
        "delegate",
    ]:
        assert ns in _EVENT_TAXONOMY_SECTION, (
            f"Event Taxonomy should cover namespace '{ns}'"
        )


# ——————————————————————————————————————————————————————
# SKILL.md — Common Navigation Patterns section
# ——————————————————————————————————————————————————————


def test_navigation_patterns_section_exists() -> None:
    assert "## Common Navigation Patterns" in _SKILL_CONTENT, (
        "SKILL.md should have a ## Common Navigation Patterns section"
    )


def test_navigation_patterns_have_code_blocks() -> None:
    """Navigation patterns contain bash code examples."""
    code_blocks = re.findall(r"```", _NAVIGATION_SECTION)
    assert len(code_blocks) >= 6, (
        f"Expected at least 3 code blocks (6 markers), found {len(code_blocks)} markers"
    )


def test_navigation_listing_sessions() -> None:
    assert re.search(
        r"list.*session|ls.*session", _NAVIGATION_SECTION, re.IGNORECASE
    ), "Navigation patterns should include listing sessions"


def test_navigation_finding_errors() -> None:
    assert re.search(r"orchestrator:error|tool:error", _NAVIGATION_SECTION), (
        "Navigation patterns should include error-finding examples (orchestrator:error or tool:error)"
    )


# ——————————————————————————————————————————————————————
# SKILL.md — Project Slug Algorithm section
# ——————————————————————————————————————————————————————


def test_slug_algorithm_section_exists() -> None:
    assert "## Project Slug Algorithm" in _SKILL_CONTENT, (
        "SKILL.md should have a ## Project Slug Algorithm section"
    )


def test_slug_algorithm_has_steps() -> None:
    """Project slug algorithm documents 5 derivation steps."""
    # Should have numbered steps or at least mention the transformation
    assert re.search(r"[1-5]\.", _SLUG_SECTION), (
        "Project Slug Algorithm should have numbered steps"
    )


def test_slug_algorithm_mentions_working_directory() -> None:
    assert re.search(r"working.dir", _SLUG_SECTION, re.IGNORECASE), (
        "Project Slug Algorithm should mention working directory"
    )


# ——————————————————————————————————————————————————————
# SKILL.md — Cross-references to context files
# ——————————————————————————————————————————————————————


def test_skill_references_event_schema() -> None:
    assert "event-schema.md" in _SKILL_CONTENT, (
        "SKILL.md should reference event-schema.md"
    )


def test_skill_references_extraction_patterns() -> None:
    assert "safe-extraction-patterns.md" in _SKILL_CONTENT, (
        "SKILL.md should reference safe-extraction-patterns.md"
    )


# ——————————————————————————————————————————————————————
# event-schema.md — File existence and structure
# ——————————————————————————————————————————————————————


def test_event_schema_file_exists() -> None:
    assert EVENT_SCHEMA_FILE.exists(), (
        f"event-schema.md not found at {EVENT_SCHEMA_FILE}"
    )


def test_event_schema_has_session_start() -> None:
    assert "session:start" in _EVENT_SCHEMA_CONTENT, (
        "event-schema.md should document session:start"
    )


def test_event_schema_has_session_fork() -> None:
    assert "session:fork" in _EVENT_SCHEMA_CONTENT, (
        "event-schema.md should document session:fork"
    )


def test_event_schema_has_session_end() -> None:
    assert "session:end" in _EVENT_SCHEMA_CONTENT, (
        "event-schema.md should document session:end"
    )


def test_event_schema_has_orchestrator_events() -> None:
    for event in ["orchestrator:start", "orchestrator:complete", "orchestrator:error"]:
        assert event in _EVENT_SCHEMA_CONTENT, (
            f"event-schema.md should document {event}"
        )


def test_event_schema_has_prompt_submit() -> None:
    assert "prompt:submit" in _EVENT_SCHEMA_CONTENT, (
        "event-schema.md should document prompt:submit"
    )


def test_event_schema_has_tool_events() -> None:
    for event in ["tool:pre", "tool:post"]:
        assert event in _EVENT_SCHEMA_CONTENT, (
            f"event-schema.md should document {event}"
        )


def test_event_schema_has_provider_events() -> None:
    for event in ["provider:request", "provider:response"]:
        assert event in _EVENT_SCHEMA_CONTENT, (
            f"event-schema.md should document {event}"
        )


def test_event_schema_has_llm_response() -> None:
    assert "llm:response" in _EVENT_SCHEMA_CONTENT, (
        "event-schema.md should document llm:response"
    )


def test_event_schema_has_recipe_events() -> None:
    for event in [
        "recipe:start",
        "recipe:step_start",
        "recipe:step_complete",
        "recipe:complete",
    ]:
        assert event in _EVENT_SCHEMA_CONTENT, (
            f"event-schema.md should document {event}"
        )


def test_event_schema_has_delegate_events() -> None:
    for event in ["delegate:start", "delegate:complete"]:
        assert event in _EVENT_SCHEMA_CONTENT, (
            f"event-schema.md should document {event}"
        )


@pytest.mark.parametrize(
    "event",
    [
        "session:start",
        "session:fork",
        "session:end",
        "orchestrator:start",
        "orchestrator:complete",
        "orchestrator:error",
        "prompt:submit",
        "tool:pre",
        "tool:post",
        "provider:request",
        "provider:response",
        "llm:response",
        "recipe:start",
        "recipe:step_start",
        "recipe:step_complete",
        "recipe:complete",
        "delegate:start",
        "delegate:complete",
    ],
)
def test_event_schema_has_field_table(event: str) -> None:
    """Each event section should have its own Field/Type/Description table."""
    section = _extract_section(_EVENT_SCHEMA_CONTENT, f"`{event}`", level="###")
    assert "| Field" in section and "| Type" in section, (
        f"event-schema.md: '{event}' section should have a Field/Type table"
    )


# ——————————————————————————————————————————————————————
# safe-extraction-patterns.md — File existence and structure
# ——————————————————————————————————————————————————————


def test_extraction_patterns_file_exists() -> None:
    assert EXTRACTION_PATTERNS_FILE.exists(), (
        f"safe-extraction-patterns.md not found at {EXTRACTION_PATTERNS_FILE}"
    )


def test_extraction_patterns_has_orientation() -> None:
    assert re.search(r"Orientation", _EXTRACTION_CONTENT, re.IGNORECASE), (
        "safe-extraction-patterns.md should have Orientation section"
    )


def test_extraction_patterns_has_session_metadata() -> None:
    assert re.search(r"Session Metadata", _EXTRACTION_CONTENT, re.IGNORECASE), (
        "safe-extraction-patterns.md should have Session Metadata section"
    )


def test_extraction_patterns_has_event_extraction() -> None:
    assert re.search(r"Event Extraction", _EXTRACTION_CONTENT, re.IGNORECASE), (
        "safe-extraction-patterns.md should have Event Extraction section"
    )


def test_extraction_patterns_has_tracing_a_turn() -> None:
    assert re.search(r"Tracing a Turn", _EXTRACTION_CONTENT, re.IGNORECASE), (
        "safe-extraction-patterns.md should have Tracing a Turn section"
    )


def test_extraction_patterns_has_session_hierarchy() -> None:
    assert re.search(r"Session Hierarchy", _EXTRACTION_CONTENT, re.IGNORECASE), (
        "safe-extraction-patterns.md should have Session Hierarchy section"
    )


def test_extraction_patterns_has_performance_safety() -> None:
    assert re.search(r"Performance|Safety", _EXTRACTION_CONTENT, re.IGNORECASE), (
        "safe-extraction-patterns.md should have Performance and Safety section"
    )


def test_extraction_patterns_has_code_blocks() -> None:
    """Extraction patterns should have plenty of code examples."""
    code_blocks = re.findall(r"```", _EXTRACTION_CONTENT)
    assert len(code_blocks) >= 12, (
        f"Expected at least 6 code blocks (12 markers), found {len(code_blocks)} markers"
    )


def test_extraction_patterns_uses_jq() -> None:
    assert "jq" in _EXTRACTION_CONTENT, (
        "safe-extraction-patterns.md should contain jq commands"
    )


def test_extraction_patterns_uses_grep() -> None:
    assert "grep" in _EXTRACTION_CONTENT, (
        "safe-extraction-patterns.md should contain grep commands"
    )


def test_extraction_patterns_uses_wc() -> None:
    assert "wc -l" in _EXTRACTION_CONTENT, (
        "safe-extraction-patterns.md should contain wc -l for orientation"
    )
