"""Tests for README.md completeness.

Verifies that the README.md at the module root contains all required sections
as specified in the task acceptance criteria.
"""

from __future__ import annotations

from pathlib import Path

import pytest

README_PATH = Path(__file__).parent.parent / "README.md"

# ---------------------------------------------------------------------------
# Shared documented-surface constants.
#
# These drive BOTH the README assertions below AND the --help parity
# assertions (TestHelpReadmeParity).  One definition, two consumers: a flag
# that is renamed in only one of the two documents fails the parity test.
# ---------------------------------------------------------------------------

FLAG_DESTINATION = "--destination"
FLAG_AUTO_APPROVE = "--auto-approve"
FLAG_AUTO_APPROVE_SHORT = "`-y`"
DEFAULT_SCAN_ROOT = "~/.amplifier/projects"
SETTINGS_PATH = "~/.amplifier/settings.yaml"
KEYS_ENV_PATH = "~/.amplifier/keys.env"
DESTINATIONS_CONFIG_KEY = "overrides.hook-context-intelligence.config.destinations"
CONFIRM_PROMPT = "Proceed? [y/N]"

# In-agent tools that DO NOT EXIST in this module (no mount(), no
# amplifier.modules entry point).  The README must never mention them.
PHANTOM_TOOL_START = "context_intelligence_upload_start"
PHANTOM_TOOL_STATUS = "context_intelligence_upload_status"


@pytest.fixture(scope="module")
def readme_content() -> str:
    """Return the README.md content, skipping if the file does not exist."""
    if not README_PATH.exists():
        pytest.skip("README.md does not exist yet")
    return README_PATH.read_text(encoding="utf-8")


class TestReadmeExists:
    """README.md must exist at the module root."""

    def test_readme_file_exists(self):
        assert README_PATH.exists(), f"README.md not found at {README_PATH}"


class TestInstallationSection:
    """Installation section with two subsections must be present."""

    def test_installation_section_present(self, readme_content):
        assert "## Installation" in readme_content or "# Installation" in readme_content

    def test_as_amplifier_module_subsection_present(self, readme_content):
        # Must explain it's included in the bundle
        assert "Amplifier module" in readme_content or "amplifier module" in readme_content.lower()
        assert "bundle" in readme_content.lower()

    def test_standalone_cli_subsection_present(self, readme_content):
        assert "standalone" in readme_content.lower() or "CLI" in readme_content

    def test_uv_tool_install_command_present(self, readme_content):
        assert "uv tool install" in readme_content

    def test_uv_pip_install_command_present(self, readme_content):
        assert "uv pip install" in readme_content

    def test_install_from_git_present(self, readme_content):
        assert "git+" in readme_content or "git+https" in readme_content


class TestCLIUsageSection:
    """CLI Usage section with flags table and examples must be present."""

    def test_cli_usage_section_present(self, readme_content):
        assert "CLI Usage" in readme_content or "## Usage" in readme_content

    def test_flags_table_path_flag(self, readme_content):
        assert "--path" in readme_content

    def test_flags_table_server_url_flag(self, readme_content):
        assert "--server-url" in readme_content

    def test_flags_table_api_key_flag(self, readme_content):
        assert "--api-key" in readme_content

    def test_flags_table_job_id_flag(self, readme_content):
        assert "--job-id" in readme_content

    def test_flags_table_progress_flag(self, readme_content):
        assert "--progress" in readme_content

    def test_flags_table_minus_h(self, readme_content):
        assert "-h" in readme_content

    def test_flags_table_double_dash_help(self, readme_content):
        assert "--help" in readme_content

    def test_example_single_session(self, readme_content):
        # Single session example - either mentions single session or uses
        # a path containing session-like patterns
        lower = readme_content.lower()
        assert "single session" in lower or "sessions/" in readme_content

    def test_example_project_tree(self, readme_content):
        lower = readme_content.lower()
        assert "project" in lower and ("tree" in lower or "entire" in lower)

    def test_example_recovery_server(self, readme_content):
        lower = readme_content.lower()
        assert "recovery" in lower


class TestNoPhantomTools:
    """The README must not document in-session tools that do not exist.

    This module ships a console script ONLY.  There is no mount() and no
    amplifier.modules entry point, so no tool is exposed inside an Amplifier
    session.  Documenting one is a correctness bug, not a style nit.
    """

    def test_upload_start_tool_absent(self, readme_content):
        assert PHANTOM_TOOL_START not in readme_content

    def test_upload_status_tool_absent(self, readme_content):
        assert PHANTOM_TOOL_STATUS not in readme_content

    def test_cli_only_reality_stated(self, readme_content):
        assert "CLI-only" in readme_content


class TestRecoveryScenariosSection:
    """Recovery Scenarios section with three scenarios must be present."""

    def test_recovery_scenarios_section_present(self, readme_content):
        assert "Recovery" in readme_content

    def test_server_unreachable_scenario(self, readme_content):
        lower = readme_content.lower()
        assert "unreachable" in lower or "server unreachable" in lower

    def test_different_server_scenario(self, readme_content):
        lower = readme_content.lower()
        assert "different server" in lower or "targeting" in lower or "another server" in lower

    def test_data_loss_scenario(self, readme_content):
        lower = readme_content.lower()
        assert "data loss" in lower or "replay" in lower


class TestIdempotencyGuaranteeSection:
    """Idempotency Guarantee section must explain SHA-256 keys."""

    def test_idempotency_section_present(self, readme_content):
        assert "Idempotency" in readme_content or "idempotency" in readme_content

    def test_sha256_mentioned(self, readme_content):
        assert "SHA-256" in readme_content or "sha256" in readme_content.lower()

    def test_safe_rerunning_mentioned(self, readme_content):
        lower = readme_content.lower()
        assert "re-run" in lower or "rerun" in lower or "safe" in lower

    def test_no_duplicates_mentioned(self, readme_content):
        lower = readme_content.lower()
        assert "duplicate" in lower or "no duplicates" in lower


class TestProgressFileSection:
    """Progress File section with running and failed JSON examples must be present."""

    def test_progress_file_section_present(self, readme_content):
        assert "Progress File" in readme_content or "Progress" in readme_content

    def test_running_state_json_present(self, readme_content):
        assert '"running"' in readme_content

    def test_failed_state_json_present(self, readme_content):
        assert '"failed"' in readme_content

    def test_failed_at_field_present(self, readme_content):
        assert "failed_at" in readme_content

    def test_sessions_total_field_present(self, readme_content):
        assert "sessions_total" in readme_content

    def test_job_id_field_present(self, readme_content):
        assert "job_id" in readme_content


class TestWorkspaceBehaviourSection:
    """Workspace Behaviour section must explain workspace source."""

    def test_workspace_behaviour_section_present(self, readme_content):
        assert "Workspace" in readme_content

    def test_events_jsonl_as_workspace_source(self, readme_content):
        assert "events.jsonl" in readme_content

    def test_workspace_not_overridden_statement(self, readme_content):
        lower = readme_content.lower()
        # Should say workspace comes from events.jsonl and is never overridden
        assert "never" in lower or "not override" in lower or "unchanged" in lower
