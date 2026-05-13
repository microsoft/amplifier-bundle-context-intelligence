"""Tests for DTU validation profile: context-intelligence-redesigned-mode-validation.yaml.

Verifies:
- File exists at expected path
- YAML parses cleanly (no syntax errors)
- profile.name == 'context-intelligence-redesigned-mode-validation'
- Exactly 2 url_rewrites entries
- Exactly 7 assertions (T1 through T7)
- Exactly 2 scenarios (scenario-A and scenario-B)
- Mandatory Gitea comment block is present in the file
- All assertion IDs T1-T7 are present
- Both scenario IDs are present
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent
PROFILE_PATH = (
    REPO_ROOT
    / ".amplifier"
    / "digital-twin-universe"
    / "profiles"
    / "context-intelligence-redesigned-mode-validation.yaml"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_profile() -> dict:
    """Load and return the parsed YAML profile."""
    with open(PROFILE_PATH) as f:
        return yaml.safe_load(f)


def load_raw() -> str:
    """Return raw file content for comment-block checks."""
    return PROFILE_PATH.read_text()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_file_exists():
    """Profile file must exist at expected path."""
    assert PROFILE_PATH.exists(), f"Profile not found at {PROFILE_PATH}"


def test_yaml_parses_cleanly():
    """YAML must be syntactically valid."""
    data = load_profile()
    assert data is not None
    assert isinstance(data, dict)


def test_profile_name():
    """profile.name must equal 'context-intelligence-redesigned-mode-validation'."""
    data = load_profile()
    assert "profile" in data, "Top-level 'profile' key missing"
    assert data["profile"]["name"] == "context-intelligence-redesigned-mode-validation"


def test_profile_has_version():
    """profile.version must be set."""
    data = load_profile()
    assert "version" in data["profile"], "profile.version missing"
    assert data["profile"]["version"] == "1.0.0"


def test_profile_has_description():
    """profile.description must be non-empty."""
    data = load_profile()
    assert "description" in data["profile"], "profile.description missing"
    assert data["profile"]["description"].strip()


def test_url_rewrites_count():
    """Exactly 2 url_rewrites entries must be present."""
    data = load_profile()
    assert "url_rewrites" in data, "url_rewrites key missing"
    rewrites = data["url_rewrites"]
    assert isinstance(rewrites, list), "url_rewrites must be a list"
    assert len(rewrites) == 2, f"Expected 2 url_rewrites, got {len(rewrites)}"


def test_url_rewrites_have_from_and_to():
    """Every url_rewrite entry must have 'from' and 'to' keys."""
    data = load_profile()
    for i, rewrite in enumerate(data["url_rewrites"]):
        assert "from" in rewrite, f"url_rewrites[{i}] missing 'from'"
        assert "to" in rewrite, f"url_rewrites[{i}] missing 'to'"


def test_url_rewrites_target_context_intelligence_bundle():
    """url_rewrites must target amplifier-bundle-context-intelligence."""
    data = load_profile()
    for rewrite in data["url_rewrites"]:
        assert "amplifier-bundle-context-intelligence" in rewrite["from"]
        assert "amplifier-bundle-context-intelligence" in rewrite["to"]


def test_url_rewrites_redirect_to_gitea():
    """url_rewrites must redirect from github.com to gitea_host."""
    data = load_profile()
    for rewrite in data["url_rewrites"]:
        assert "github.com" in rewrite["from"]
        assert "{gitea_host}" in rewrite["to"]


def test_mandatory_gitea_comment_present():
    """Mandatory Gitea url_rewrite comment block must be present in raw file."""
    raw = load_raw()
    # The comment must explain why url_rewrites is non-optional
    assert "MANDATORY" in raw or "mandatory" in raw or "non-optional" in raw, (
        "Mandatory Gitea comment block not found in profile file"
    )


def test_install_section_present():
    """install section must be present with a command."""
    data = load_profile()
    assert "install" in data, "install key missing"
    assert "command" in data["install"], "install.command missing"
    cmd = data["install"]["command"]
    assert "bundle add" in cmd
    assert "amplifier-bundle-context-intelligence" in cmd
    assert "--app" in cmd


def test_assertions_count():
    """Exactly 7 assertions must be present."""
    data = load_profile()
    assert "assertions" in data, "assertions key missing"
    assertions = data["assertions"]
    assert isinstance(assertions, list), "assertions must be a list"
    assert len(assertions) == 7, f"Expected 7 assertions, got {len(assertions)}"


def test_assertion_ids_t1_through_t7():
    """All assertion IDs T1 through T7 must be present."""
    data = load_profile()
    ids = {a["id"] for a in data["assertions"]}
    expected_prefixes = [f"T{n}" for n in range(1, 8)]
    for prefix in expected_prefixes:
        matching = [i for i in ids if i.startswith(prefix + "-") or i == prefix]
        assert matching, f"No assertion with ID starting with '{prefix}' found in {ids}"


def test_assertions_have_required_fields():
    """Each assertion must have id, description, when, and expect."""
    data = load_profile()
    for assertion in data["assertions"]:
        assert "id" in assertion, f"Assertion missing 'id': {assertion}"
        assert "description" in assertion, f"Assertion {assertion['id']} missing 'description'"
        assert "when" in assertion, f"Assertion {assertion['id']} missing 'when'"
        assert "expect" in assertion, f"Assertion {assertion['id']} missing 'expect'"


def test_scenarios_count():
    """Exactly 2 scenarios must be present."""
    data = load_profile()
    assert "scenarios" in data, "scenarios key missing"
    scenarios = data["scenarios"]
    assert isinstance(scenarios, list), "scenarios must be a list"
    assert len(scenarios) == 2, f"Expected 2 scenarios, got {len(scenarios)}"


def test_scenario_ids():
    """scenario-A-concept-elicitation and scenario-B-signal-gap-loop must be present."""
    data = load_profile()
    ids = {s["id"] for s in data["scenarios"]}
    assert "scenario-A-concept-elicitation" in ids, (
        f"scenario-A-concept-elicitation not found in {ids}"
    )
    assert "scenario-B-signal-gap-loop" in ids, f"scenario-B-signal-gap-loop not found in {ids}"


def test_scenario_a_has_required_fields():
    """scenario-A must have description, user_message, and pass_criteria."""
    data = load_profile()
    scenario_a = next(s for s in data["scenarios"] if s["id"] == "scenario-A-concept-elicitation")
    assert "description" in scenario_a
    assert "user_message" in scenario_a
    assert "pass_criteria" in scenario_a


def test_scenario_a_forbidden_substrings():
    """scenario-A pass_criteria must have forbidden_substrings list."""
    data = load_profile()
    scenario_a = next(s for s in data["scenarios"] if s["id"] == "scenario-A-concept-elicitation")
    pc = scenario_a["pass_criteria"]
    assert "forbidden_substrings" in pc
    forbidden = pc["forbidden_substrings"]
    assert isinstance(forbidden, list)
    expected_forbidden = [
        "graph-analyst",
        "session-navigator",
        "context-intelligence-design-facilitator",
        "context-intelligence-tool-designer",
    ]
    for item in expected_forbidden:
        assert item in forbidden, f"'{item}' not in forbidden_substrings"


def test_scenario_b_has_required_fields():
    """scenario-B must have description, setup, and pass_criteria."""
    data = load_profile()
    scenario_b = next(s for s in data["scenarios"] if s["id"] == "scenario-B-signal-gap-loop")
    assert "description" in scenario_b
    assert "setup" in scenario_b
    assert "pass_criteria" in scenario_b


def test_scenario_b_pass_criteria():
    """scenario-B pass_criteria must have file_exists, entry_fields, eventual_status."""
    data = load_profile()
    scenario_b = next(s for s in data["scenarios"] if s["id"] == "scenario-B-signal-gap-loop")
    pc = scenario_b["pass_criteria"]
    assert "file_exists" in pc
    assert ".context-intelligence-investigation/signal-gaps.md" in pc["file_exists"]
    assert "entry_fields" in pc
    assert "eventual_status" in pc
    assert pc["eventual_status"] == "resolved"
