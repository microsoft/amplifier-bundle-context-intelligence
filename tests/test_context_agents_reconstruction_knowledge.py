"""Tests for context/agents/reconstruction-knowledge.md (task-16).

Verifies that the agent context file exists and contains all required sections:
- Tool Location
- Safe Invocation Patterns
- What It Creates (table)
- When to Guide vs Invoke
- Other CLI Subcommands (table)

Also verifies key content within each section.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DOC_PATH = REPO_ROOT / "context" / "agents" / "reconstruction-knowledge.md"


def _doc_content() -> str:
    return DOC_PATH.read_text()


class TestDocumentExists:
    """The file must exist at context/agents/reconstruction-knowledge.md."""

    def test_file_exists(self) -> None:
        assert DOC_PATH.exists(), f"Expected {DOC_PATH} to exist"

    def test_file_is_not_empty(self) -> None:
        assert DOC_PATH.stat().st_size > 0, f"Expected {DOC_PATH} to be non-empty"


class TestToolLocationSection:
    """The document must contain a Tool Location section with the script path."""

    def test_has_tool_location_section(self) -> None:
        assert "Tool Location" in _doc_content()

    def test_mentions_scripts_context_intelligence_py(self) -> None:
        assert "scripts/context-intelligence.py" in _doc_content()

    def test_has_submodule_invocation_example(self) -> None:
        content = _doc_content()
        # Must show how to invoke reconstruct subcommand
        assert "reconstruct" in content


class TestSafeInvocationPatternsSection:
    """The document must contain a Safe Invocation Patterns section."""

    def test_has_safe_invocation_patterns_section(self) -> None:
        assert "Safe Invocation Patterns" in _doc_content()

    def test_mentions_dry_run_first(self) -> None:
        assert "--dry-run" in _doc_content()

    def test_mentions_metadata_only_for_fastest_fix(self) -> None:
        assert "--metadata-only" in _doc_content()

    def test_mentions_single_session_targeted(self) -> None:
        assert "--session" in _doc_content()

    def test_mentions_full_reconstruction_with_force(self) -> None:
        assert "--force" in _doc_content()


class TestWhatItCreatesSection:
    """The document must contain a What It Creates table."""

    def test_has_what_it_creates_section(self) -> None:
        assert "What It Creates" in _doc_content()

    def test_documents_events_jsonl(self) -> None:
        assert "events.jsonl" in _doc_content()

    def test_events_jsonl_mentions_size_range(self) -> None:
        content = _doc_content()
        # 10-200KB size range
        assert "10" in content and "200" in content and "KB" in content

    def test_documents_transcript_jsonl(self) -> None:
        assert "transcript.jsonl" in _doc_content()

    def test_transcript_jsonl_mentions_size_range(self) -> None:
        content = _doc_content()
        # 5-100KB size range
        assert "5" in content and "100" in content

    def test_documents_metadata_json(self) -> None:
        assert "metadata.json" in _doc_content()

    def test_metadata_json_mentions_under_1kb(self) -> None:
        content = _doc_content()
        assert "<1KB" in content or "< 1KB" in content or "1KB" in content

    def test_mentions_workspace_slug_path(self) -> None:
        content = _doc_content()
        assert "workspace-slug" in content or "{workspace-slug}" in content

    def test_mentions_session_id_path(self) -> None:
        content = _doc_content()
        assert "session-id" in content or "{session-id}" in content

    def test_mentions_amplifier_projects_path(self) -> None:
        assert "~/.amplifier/projects" in _doc_content()


class TestWhenToGuideVsInvokeSection:
    """The document must contain a When to Guide vs Invoke section."""

    def test_has_when_to_guide_vs_invoke_section(self) -> None:
        content = _doc_content()
        assert "When to Guide" in content and "Invoke" in content

    def test_mentions_guide_for_missing_sessions(self) -> None:
        content = _doc_content()
        assert "missing" in content.lower()

    def test_mentions_invoke_via_bash_only_when_explicitly_asked(self) -> None:
        content = _doc_content()
        assert "bash" in content.lower()
        assert "explicit" in content.lower()

    def test_mentions_never_run_force_unless_explicitly_requested(self) -> None:
        content = _doc_content()
        assert "--force" in content
        # Must say not to use force unless asked
        assert "never" in content.lower() or "only" in content.lower()


class TestOtherCLISubcommandsSection:
    """The document must contain an Other CLI Subcommands table."""

    def test_has_other_cli_subcommands_section(self) -> None:
        assert "Other CLI Subcommands" in _doc_content()

    def test_documents_status_subcommand(self) -> None:
        assert "status" in _doc_content()

    def test_documents_query_subcommand(self) -> None:
        assert "query" in _doc_content()

    def test_documents_upload_subcommand(self) -> None:
        assert "upload" in _doc_content()
