"""Tests for context/session-reconstruction.md (task-15).

Verifies that the reference document exists and contains all required sections:
- When to Use
- Prerequisites
- CLI Reference
- Output Files
- Known Limitations

Also verifies all CLI flags and output files are documented.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DOC_PATH = REPO_ROOT / "context" / "session-reconstruction.md"


class TestDocumentExists:
    """The file must exist at context/session-reconstruction.md."""

    def test_file_exists(self) -> None:
        assert DOC_PATH.exists(), f"Expected {DOC_PATH} to exist"

    def test_file_is_not_empty(self) -> None:
        assert DOC_PATH.stat().st_size > 0, f"Expected {DOC_PATH} to be non-empty"


class TestRequiredSections:
    """The document must contain all required sections."""

    @classmethod
    def _content(cls) -> str:
        return DOC_PATH.read_text()

    def test_has_when_to_use_section(self) -> None:
        assert "## When to Use" in self._content()

    def test_has_prerequisites_section(self) -> None:
        assert "## Prerequisites" in self._content()

    def test_has_cli_reference_section(self) -> None:
        assert "## CLI Reference" in self._content()

    def test_has_output_files_section(self) -> None:
        assert "## Output Files" in self._content()

    def test_has_known_limitations_section(self) -> None:
        assert "## Known Limitations" in self._content()


class TestWhenToUseContent:
    """The When to Use section must cover all specified use cases."""

    @classmethod
    def _content(cls) -> str:
        return DOC_PATH.read_text()

    def test_mentions_missing_files(self) -> None:
        assert "Missing files" in self._content() or "missing files" in self._content()

    def test_mentions_corrupted_files(self) -> None:
        assert "Corrupted files" in self._content() or "corrupted files" in self._content()

    def test_mentions_migration(self) -> None:
        assert "Migration" in self._content() or "migration" in self._content()

    def test_mentions_resume_list_repair(self) -> None:
        assert "resume" in self._content().lower() and "repair" in self._content().lower()


class TestPrerequisitesContent:
    """The Prerequisites section must cover all required prerequisites."""

    @classmethod
    def _content(cls) -> str:
        return DOC_PATH.read_text()

    def test_mentions_graph_server(self) -> None:
        content = self._content()
        assert "graph server" in content.lower() or "graph" in content.lower()

    def test_mentions_api_key(self) -> None:
        assert "API key" in self._content() or "api key" in self._content().lower()

    def test_mentions_hook(self) -> None:
        assert "hook" in self._content().lower()


class TestCLIReferenceFlags:
    """All CLI flags must be documented in the CLI Reference section."""

    @classmethod
    def _content(cls) -> str:
        return DOC_PATH.read_text()

    def test_documents_project_dir_flag(self) -> None:
        assert "--project-dir" in self._content()

    def test_documents_events_only_flag(self) -> None:
        assert "--events-only" in self._content()

    def test_documents_transcript_only_flag(self) -> None:
        assert "--transcript-only" in self._content()

    def test_documents_metadata_only_flag(self) -> None:
        assert "--metadata-only" in self._content()

    def test_documents_force_flag(self) -> None:
        assert "--force" in self._content()

    def test_documents_dry_run_flag(self) -> None:
        assert "--dry-run" in self._content()

    def test_documents_resolve_blobs_flag(self) -> None:
        assert "--resolve-blobs" in self._content()

    def test_documents_session_flag(self) -> None:
        assert "--session" in self._content()

    def test_documents_verbose_flag(self) -> None:
        assert "--verbose" in self._content()

    def test_documents_server_url_flag(self) -> None:
        assert "--server-url" in self._content()

    def test_documents_api_key_flag(self) -> None:
        assert "--api-key" in self._content()


class TestOutputFilesContent:
    """All output files must be documented in the Output Files section."""

    @classmethod
    def _content(cls) -> str:
        return DOC_PATH.read_text()

    def test_documents_events_jsonl(self) -> None:
        assert "events.jsonl" in self._content()

    def test_documents_transcript_jsonl(self) -> None:
        assert "transcript.jsonl" in self._content()

    def test_documents_metadata_json(self) -> None:
        assert "metadata.json" in self._content()

    def test_events_jsonl_mentions_hook_logging_format(self) -> None:
        content = self._content()
        assert "hook" in content.lower() and "logging" in content.lower()

    def test_transcript_jsonl_mentions_sessionstore_format(self) -> None:
        content = self._content()
        assert "sessionstore" in content.lower() or "SessionStore" in content


class TestKnownLimitationsContent:
    """The Known Limitations section must cover all specified limitations."""

    @classmethod
    def _content(cls) -> str:
        return DOC_PATH.read_text()

    def test_mentions_streaming_telemetry(self) -> None:
        content = self._content()
        assert "streaming" in content.lower() or "content_block" in content

    def test_mentions_approximately_39_percent(self) -> None:
        # ~39% mentioned in the spec
        assert "39" in self._content()

    def test_mentions_delegate_events(self) -> None:
        content = self._content()
        assert "delegate" in content.lower()

    def test_mentions_session_names_approximations(self) -> None:
        content = self._content()
        assert "approximation" in content.lower() or "approximations" in content.lower()

    def test_mentions_unknown_bundle(self) -> None:
        assert "unknown" in self._content().lower()
