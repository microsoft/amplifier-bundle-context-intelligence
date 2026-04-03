"""Tests for the blob-reading skill (SKILL.md).

Validates that SKILL.md exists with correct structure, YAML frontmatter,
all 5 required content sections, safe extraction patterns, and critical
rules for safe ci-blob:// URI resolution.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BUNDLE_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = BUNDLE_ROOT / "skills" / "blob-reading"
SKILL_FILE = SKILL_DIR / "SKILL.md"

# ---------------------------------------------------------------------------
# Content loading
# ---------------------------------------------------------------------------

try:
    _SKILL_CONTENT: str = SKILL_FILE.read_text() if SKILL_FILE.exists() else ""
except OSError:
    _SKILL_CONTENT = ""


def _parse_frontmatter(content: str) -> dict:
    """Extract YAML frontmatter from markdown content."""
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return {}
    return yaml.safe_load(match.group(1))


def _extract_section(content: str, heading: str, level: str = "##") -> str:
    """Extract content under a markdown heading, up to the next heading of same level or end."""
    escaped = re.escape(heading)
    escaped_level = re.escape(level)
    pattern = rf"^{escaped_level} {escaped}\s*\n(.*?)(?=^{escaped_level} |\Z)"
    match = re.search(pattern, content, re.MULTILINE | re.DOTALL)
    if not match:
        return ""
    return match.group(1)


_FRONTMATTER: dict = _parse_frontmatter(_SKILL_CONTENT) if _SKILL_CONTENT else {}

# Pre-extract sections.
_WHEN_TO_USE_SECTION = _extract_section(_SKILL_CONTENT, "When to Use blob_read")
_HOW_TO_RESOLVE_SECTION = _extract_section(_SKILL_CONTENT, "How to Resolve a Blob")
_SAFE_EXTRACTION_SECTION = _extract_section(_SKILL_CONTENT, "Safe Extraction Patterns")
_CRITICAL_RULES_SECTION = _extract_section(_SKILL_CONTENT, "Critical Rules")
_BLOB_DETECTION_SECTION = _extract_section(_SKILL_CONTENT, "Blob Field Detection")


# ===========================================================================
# File and directory existence
# ===========================================================================


def test_skill_file_exists() -> None:
    assert SKILL_FILE.exists(), f"SKILL.md not found at {SKILL_FILE}"


def test_skill_directory_exists() -> None:
    assert SKILL_DIR.is_dir(), f"Skill directory not found at {SKILL_DIR}"


# ===========================================================================
# YAML frontmatter
# ===========================================================================


def test_frontmatter_name() -> None:
    assert _FRONTMATTER.get("name") == "blob-reading", (
        f"Expected name='blob-reading', got: {_FRONTMATTER.get('name')!r}"
    )


def test_frontmatter_version() -> None:
    assert _FRONTMATTER.get("version") == "1.0.0", (
        f"Expected version='1.0.0', got: {_FRONTMATTER.get('version')!r}"
    )


def test_frontmatter_license() -> None:
    assert _FRONTMATTER.get("license") == "MIT"


def test_frontmatter_description_present() -> None:
    desc = _FRONTMATTER.get("description", "")
    assert len(desc) > 0, "Frontmatter description must be non-empty"


def test_frontmatter_description_mentions_blob() -> None:
    desc = _FRONTMATTER.get("description", "")
    assert re.search(r"ci-blob", desc, re.IGNORECASE), (
        "Frontmatter description should mention ci-blob:// URIs"
    )


def test_frontmatter_is_first_content() -> None:
    """YAML frontmatter must start at the very first line."""
    assert _SKILL_CONTENT.startswith("---\n"), (
        "SKILL.md must begin with '---' YAML frontmatter delimiter"
    )


# ===========================================================================
# Section 1: When to Use blob_read
# ===========================================================================


def test_when_to_use_section_exists() -> None:
    assert "## When to Use blob_read" in _SKILL_CONTENT, (
        "SKILL.md must have a '## When to Use blob_read' section"
    )


def test_when_to_use_has_do_resolve_guidance() -> None:
    assert re.search(r"Do resolve", _WHEN_TO_USE_SECTION, re.IGNORECASE), (
        "'When to Use' section should include 'Do resolve' guidance"
    )


def test_when_to_use_has_do_not_resolve_guidance() -> None:
    assert re.search(r"Do NOT resolve", _WHEN_TO_USE_SECTION, re.IGNORECASE), (
        "'When to Use' section should include 'Do NOT resolve' guidance"
    )


def test_when_to_use_mentions_metadata_first() -> None:
    """Most queries don't need blobs — the section should say to exhaust metadata first."""
    assert re.search(r"metadata|structured", _WHEN_TO_USE_SECTION, re.IGNORECASE), (
        "'When to Use' should mention exhausting metadata/structured data before resolving"
    )


# ===========================================================================
# Section 2: How to Resolve a Blob (4-step procedure)
# ===========================================================================


def test_how_to_resolve_section_exists() -> None:
    assert "## How to Resolve a Blob" in _SKILL_CONTENT, (
        "SKILL.md must have a '## How to Resolve a Blob' section"
    )


def test_how_to_resolve_has_four_steps() -> None:
    """The resolution procedure must document exactly 4 sequential steps."""
    steps = re.findall(r"\*\*Step\s+[1-4]", _HOW_TO_RESOLVE_SECTION)
    assert len(steps) >= 4, (
        f"'How to Resolve' should have Steps 1–4, found markers: {steps}"
    )


def test_how_to_resolve_step1_identify_uri() -> None:
    assert re.search(r"Identify|URI", _HOW_TO_RESOLVE_SECTION, re.IGNORECASE), (
        "Step 1 should cover identifying the ci-blob:// URI"
    )


def test_how_to_resolve_step2_call_blob_read() -> None:
    assert "blob_read" in _HOW_TO_RESOLVE_SECTION, (
        "Step 2 should reference calling blob_read"
    )


def test_how_to_resolve_step3_file_path() -> None:
    assert re.search(r"file path|local file", _HOW_TO_RESOLVE_SECTION, re.IGNORECASE), (
        "Step 3 should cover getting the file path back from blob_read"
    )


def test_how_to_resolve_step4_read_selectively() -> None:
    assert re.search(r"selectively|jq|head", _HOW_TO_RESOLVE_SECTION, re.IGNORECASE), (
        "Step 4 should cover reading the file selectively"
    )


def test_how_to_resolve_has_code_example() -> None:
    """Resolution section must include a blob_read call code example."""
    assert "ci-blob://" in _HOW_TO_RESOLVE_SECTION, (
        "'How to Resolve' should include a ci-blob:// URI example"
    )


# ===========================================================================
# Section 3: Safe Extraction Patterns
# ===========================================================================


def test_safe_extraction_section_exists() -> None:
    assert "## Safe Extraction Patterns" in _SKILL_CONTENT, (
        "SKILL.md must have a '## Safe Extraction Patterns' section"
    )


def test_safe_extraction_has_size_check() -> None:
    """Must document checking size with ls -lh or wc -c before reading."""
    assert re.search(r"ls -lh|wc -c", _SAFE_EXTRACTION_SECTION), (
        "'Safe Extraction Patterns' must show ls -lh or wc -c size check"
    )


def test_safe_extraction_has_jq_field_extraction() -> None:
    assert (
        "jq '.field_name'" in _SAFE_EXTRACTION_SECTION
        or "jq '." in _SAFE_EXTRACTION_SECTION
    ), "'Safe Extraction Patterns' must include jq field extraction example"


def test_safe_extraction_has_keys_exploration() -> None:
    assert "jq 'keys'" in _SAFE_EXTRACTION_SECTION, (
        "'Safe Extraction Patterns' must include 'jq keys' safe exploration pattern"
    )


def test_safe_extraction_has_nested_field() -> None:
    assert re.search(r"jq '\..*\[", _SAFE_EXTRACTION_SECTION), (
        "'Safe Extraction Patterns' must include nested field extraction with jq"
    )


def test_safe_extraction_has_size_guard() -> None:
    """Must document a size guard (head -c) to prevent context overflow."""
    assert "head -c" in _SAFE_EXTRACTION_SECTION, (
        "'Safe Extraction Patterns' must include 'head -c' size guard pattern"
    )


def test_safe_extraction_has_code_blocks() -> None:
    """Section must contain bash code blocks."""
    code_blocks = re.findall(r"```", _SAFE_EXTRACTION_SECTION)
    assert len(code_blocks) >= 6, (
        f"'Safe Extraction Patterns' should have at least 3 code blocks (6 markers), "
        f"found {len(code_blocks)} markers"
    )


# ===========================================================================
# Section 4: Critical Rules
# ===========================================================================


def test_critical_rules_section_exists() -> None:
    assert "## Critical Rules" in _SKILL_CONTENT, (
        "SKILL.md must have a '## Critical Rules' section"
    )


def test_critical_rules_has_five_rules() -> None:
    """Critical Rules section must document at least 5 rules."""
    rules = re.findall(r"^\d+\.", _CRITICAL_RULES_SECTION, re.MULTILINE)
    assert len(rules) >= 5, (
        f"'Critical Rules' should have at least 5 numbered rules, found {len(rules)}"
    )


def test_critical_rules_never_dump_full_blob() -> None:
    assert re.search(
        r"Never dump|never.*cat|do not.*cat", _CRITICAL_RULES_SECTION, re.IGNORECASE
    ), "Critical Rules must include a 'never dump the full blob' rule"


def test_critical_rules_check_ci_blob_prefix() -> None:
    assert re.search(r"ci-blob://", _CRITICAL_RULES_SECTION), (
        "Critical Rules must reference the ci-blob:// prefix check"
    )


def test_critical_rules_prefer_targeted_extraction() -> None:
    assert re.search(
        r"targeted|specific.field", _CRITICAL_RULES_SECTION, re.IGNORECASE
    ), "Critical Rules must mention preferring targeted extraction"


def test_critical_rules_check_size_first() -> None:
    assert re.search(r"size|ls -lh|wc", _CRITICAL_RULES_SECTION, re.IGNORECASE), (
        "Critical Rules must include a rule about checking file size first"
    )


def test_critical_rules_file_path_is_temporary() -> None:
    assert re.search(r"temporary|temp", _CRITICAL_RULES_SECTION, re.IGNORECASE), (
        "Critical Rules must note that the returned file path is temporary"
    )


# ===========================================================================
# Section 5: Blob Field Detection
# ===========================================================================


def test_blob_detection_section_exists() -> None:
    assert "## Blob Field Detection" in _SKILL_CONTENT, (
        "SKILL.md must have a '## Blob Field Detection' section"
    )


def test_blob_detection_has_json_example() -> None:
    """Section must show a JSON example with a ci-blob:// URI as a field value."""
    assert "ci-blob://" in _BLOB_DETECTION_SECTION, (
        "'Blob Field Detection' must include a JSON snippet containing a ci-blob:// URI"
    )


def test_blob_detection_mentions_parse_json_first() -> None:
    assert re.search(
        r"parse.*JSON|JSON.*first", _BLOB_DETECTION_SECTION, re.IGNORECASE
    ), "'Blob Field Detection' must instruct to parse the node result as JSON first"


def test_blob_detection_explains_not_every_field_has_blob() -> None:
    """Key insight: small payloads are inlined, only large ones become blobs."""
    assert re.search(
        r"not every|small payload|inlined|inline",
        _BLOB_DETECTION_SECTION,
        re.IGNORECASE,
    ), "'Blob Field Detection' should clarify that not every data field contains a blob"


def test_blob_detection_check_prefix_before_resolving() -> None:
    assert re.search(
        r"check.*prefix|prefix.*before|starts with",
        _BLOB_DETECTION_SECTION,
        re.IGNORECASE,
    ), (
        "'Blob Field Detection' must say to check the ci-blob:// prefix before calling blob_read"
    )


# ===========================================================================
# Content coherence checks
# ===========================================================================


def test_skill_mentions_blob_read_tool() -> None:
    """blob_read must be mentioned as the tool to use (not some other API)."""
    assert "blob_read" in _SKILL_CONTENT, "SKILL.md must reference the blob_read tool"


def test_skill_uses_ci_blob_uri_format() -> None:
    """ci-blob:// URI format must be documented in the skill."""
    assert "ci-blob://" in _SKILL_CONTENT, (
        "SKILL.md must document the ci-blob:// URI format"
    )


def test_skill_has_horizontal_rule_dividers() -> None:
    """Sections should be separated by horizontal rule dividers."""
    dividers = re.findall(r"^---$", _SKILL_CONTENT, re.MULTILINE)
    assert len(dividers) >= 4, (
        f"SKILL.md should have at least 4 horizontal rule dividers between sections, "
        f"found {len(dividers)}"
    )
