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


class TestInstallationAccuracy:
    """The Installation section must accurately reflect what each path installs.

    ``amplifier bundle add`` brings this module into an Amplifier installation,
    but the module ships no ``mount()`` and no ``amplifier.modules`` entry
    point -- so bundle add never places ``context-intelligence-upload`` on
    ``PATH``. Only the standalone `uv tool install` command does that. The
    README must say this explicitly rather than implying the CLI comes from
    the bundle.
    """

    UV_TOOL_INSTALL_CMD = (
        'uv tool install "amplifier-module-tool-context-intelligence-upload '
        "@ git+https://github.com/microsoft/amplifier-bundle-context-intelligence@main"
        '#subdirectory=modules/tool-context-intelligence-upload"'
    )
    BUNDLE_ADD_NO_PATH_CAVEAT = (
        "does not place the `context-intelligence-upload` command on your `PATH`"
    )

    def test_uv_tool_install_command_present(self, readme_content):
        assert self.UV_TOOL_INSTALL_CMD in readme_content

    def test_bundle_add_does_not_install_cli_on_path(self, readme_content):
        assert self.BUNDLE_ADD_NO_PATH_CAVEAT in readme_content


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


class TestZeroArgGestureSection:
    """The zero-argument gesture must be documented."""

    def test_zero_argument_section_present(self, readme_content):
        assert "## Zero-Argument Usage" in readme_content

    def test_zero_arg_invocation_shown(self, readme_content):
        assert "$ context-intelligence-upload\n" in readme_content


class TestConnectionResolutionSection:
    """Where connection config comes from must be documented."""

    def test_connection_resolution_section_present(self, readme_content):
        assert "## Where Connection Config Comes From" in readme_content

    def test_settings_path_documented(self, readme_content):
        assert SETTINGS_PATH in readme_content

    def test_keys_env_path_documented(self, readme_content):
        assert KEYS_ENV_PATH in readme_content

    def test_destinations_config_key_documented(self, readme_content):
        assert DESTINATIONS_CONFIG_KEY in readme_content

    def test_var_expansion_documented(self, readme_content):
        # NOTE: a bare "${VAR}" already appears in the auth section, so this
        # asserts the keys.env-backed sentence specifically.
        assert f"expanded from `{KEYS_ENV_PATH}`" in readme_content

    def test_amplifier_home_only_documented(self, readme_content):
        assert "Amplifier home only" in readme_content

    def test_project_local_not_consulted_documented(self, readme_content):
        assert "not consulted" in readme_content

    def test_zero_destinations_error_documented(self, readme_content):
        assert "no destinations are configured" in readme_content


class TestDestinationSelectionSection:
    """Selection semantics (1 = silent, 2+ = prompt, --destination) documented."""

    def test_destination_selection_section_present(self, readme_content):
        assert "## Choosing a Destination" in readme_content

    def test_single_destination_no_prompt_documented(self, readme_content):
        assert "no prompt" in readme_content

    def test_destination_flag_documented(self, readme_content):
        assert FLAG_DESTINATION in readme_content

    def test_non_interactive_ambiguity_documented(self, readme_content):
        # NOTE: "non-interactive" alone already appears in the Entra auth note,
        # so this asserts the ambiguity row of the selection table.
        assert "Two or more, **non-interactive**" in readme_content


class TestAutoDiscoverySection:
    """Auto-discovery, the default scan root, and per-format layouts."""

    def test_auto_discovery_section_present(self, readme_content):
        assert "## Session Auto-Discovery" in readme_content

    def test_default_scan_root_documented(self, readme_content):
        # NOTE: the bare path already appears in the old examples, so this
        # asserts the auto-discovery sentence specifically.
        assert f"discovers sessions under `{DEFAULT_SCAN_ROOT}`" in readme_content

    def test_path_omitted_documented(self, readme_content):
        assert "When `--path` is omitted" in readme_content


class TestDestinationFilteringSection:
    """include/exclude filtering by the session's recorded working_dir."""

    def test_filtering_section_present(self, readme_content):
        assert "## Destination Filtering" in readme_content

    def test_recorded_working_dir_is_the_discriminator(self, readme_content):
        assert "recorded `working_dir`" in readme_content

    def test_path_is_not_the_discriminator(self, readme_content):
        assert "`--path` never decides filtering" in readme_content

    def test_legacy_approximation_documented(self, readme_content):
        assert "approximate" in readme_content

    def test_filtered_out_count_documented(self, readme_content):
        assert "filtered-out" in readme_content

    def test_raw_flags_skip_filtering_documented(self, readme_content):
        assert "no filtering is applied" in readme_content


class TestPreviewConfirmSection:
    """Preview + confirmation, and the automation escape hatch."""

    def test_preview_section_present(self, readme_content):
        assert "## Preview and Confirmation" in readme_content

    def test_confirm_prompt_documented(self, readme_content):
        assert CONFIRM_PROMPT in readme_content

    def test_auto_approve_flag_documented(self, readme_content):
        assert FLAG_AUTO_APPROVE in readme_content

    def test_auto_approve_short_flag_documented(self, readme_content):
        assert FLAG_AUTO_APPROVE_SHORT in readme_content

    def test_non_tty_without_auto_approve_errors(self, readme_content):
        # NOTE: "exit code 2" alone already appears in the legacy-format
        # section, so this asserts the preview table's row specifically.
        assert "Error telling you to pass `--auto-approve`, **exit code 2**" in readme_content


class TestProgressOutputSection:
    """Two-level TTY-aware progress and the final summary."""

    def test_progress_output_section_present(self, readme_content):
        assert "## Progress Output" in readme_content

    def test_two_level_progress_documented(self, readme_content):
        assert "two-level" in readme_content

    def test_piped_fallback_documented(self, readme_content):
        assert "one plain line per session" in readme_content

    def test_final_summary_documented(self, readme_content):
        assert "final summary" in readme_content


class TestNonGoalsSection:
    """v1 non-goals must be stated so users do not expect fan-out."""

    def test_non_goals_section_present(self, readme_content):
        assert "## Non-Goals (v1)" in readme_content

    def test_no_fanout_documented(self, readme_content):
        assert "no fan-out" in readme_content

    def test_no_all_flag_documented(self, readme_content):
        assert "`--all`" in readme_content


class TestHelpReadmeParity:
    """`--help` and README.md must document the same surface.

    Both documents are asserted against the SAME module-level constants, so a
    flag that is renamed, dropped, or added in only one of the two fails here.
    Invokes the real parser and captures the SystemExit-on-help path.
    """

    @pytest.fixture
    def detailed_help_output(self, capsys) -> str:
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit):
            _build_parser().parse_args(["--help"])
        return capsys.readouterr().out

    def test_help_documents_destination_flag(self, detailed_help_output):
        assert FLAG_DESTINATION in detailed_help_output

    def test_help_documents_auto_approve_flag(self, detailed_help_output):
        assert FLAG_AUTO_APPROVE in detailed_help_output

    def test_help_documents_auto_approve_short_flag(self, detailed_help_output):
        assert " -y" in detailed_help_output

    def test_help_documents_settings_path(self, detailed_help_output):
        assert SETTINGS_PATH in detailed_help_output

    def test_help_documents_keys_env_path(self, detailed_help_output):
        assert KEYS_ENV_PATH in detailed_help_output

    def test_help_documents_default_scan_root(self, detailed_help_output):
        assert DEFAULT_SCAN_ROOT in detailed_help_output

    def test_help_documents_confirm_prompt(self, detailed_help_output):
        assert CONFIRM_PROMPT in detailed_help_output

    def test_help_documents_destinations_config_key(self, detailed_help_output):
        assert "destinations" in detailed_help_output

    def test_help_does_not_document_phantom_start_tool(self, detailed_help_output):
        assert PHANTOM_TOOL_START not in detailed_help_output

    def test_help_does_not_document_phantom_status_tool(self, detailed_help_output):
        assert PHANTOM_TOOL_STATUS not in detailed_help_output

    def test_readme_and_help_agree_on_destination_flag(self, readme_content, detailed_help_output):
        assert (FLAG_DESTINATION in readme_content) == (FLAG_DESTINATION in detailed_help_output)

    def test_readme_and_help_agree_on_auto_approve_flag(self, readme_content, detailed_help_output):
        assert (FLAG_AUTO_APPROVE in readme_content) == (FLAG_AUTO_APPROVE in detailed_help_output)
