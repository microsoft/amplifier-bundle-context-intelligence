"""Tests for context_intelligence.reconstruct.metadata (task-7).

Covers:
- Module imports correctly (extract_metadata, build_disk_only_metadata)
- _SUBSESSION_ID_RE regex for subsession IDs
- _extract_model_from_config() finds priority==0 provider's default_model
- _find_session_start_blob_key() finds session_start raw blob key
- _find_first_llm_request_blob_key() finds earliest llm_request raw blob key
- build_disk_only_metadata() builds metadata from disk files
- _generate_session_name() from first prompt_preview truncated to 50 chars
- _build_subsession_metadata() with parent_id/trace_id/agent_name/child_span
- _build_root_metadata() resolving session_start blob with fallback chain
- extract_metadata() querying Session node, parsing parent_id, counting OrchestratorRun,
  detecting root vs subsession, generating session name
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock


class TestImport:
    """Module must be importable with the required public API."""

    def test_extract_metadata_import(self):
        """extract_metadata must be importable from context_intelligence.reconstruct.metadata."""
        from context_intelligence.reconstruct.metadata import extract_metadata  # noqa: F401

    def test_build_disk_only_metadata_import(self):
        """build_disk_only_metadata must be importable from context_intelligence.reconstruct.metadata."""
        from context_intelligence.reconstruct.metadata import build_disk_only_metadata  # noqa: F401

    def test_acceptance_criteria_command(self):
        """Simulate the acceptance criteria import command."""
        from context_intelligence.reconstruct.metadata import (
            build_disk_only_metadata,
            extract_metadata,
        )

        assert extract_metadata is not None
        assert build_disk_only_metadata is not None

    def test_subsession_id_re_import(self):
        """_SUBSESSION_ID_RE must be importable."""
        from context_intelligence.reconstruct.metadata import _SUBSESSION_ID_RE  # noqa: F401

    def test_extract_model_from_config_import(self):
        """_extract_model_from_config must be importable."""
        from context_intelligence.reconstruct.metadata import _extract_model_from_config  # noqa: F401

    def test_find_session_start_blob_key_import(self):
        """_find_session_start_blob_key must be importable."""
        from context_intelligence.reconstruct.metadata import _find_session_start_blob_key  # noqa: F401

    def test_find_first_llm_request_blob_key_import(self):
        """_find_first_llm_request_blob_key must be importable."""
        from context_intelligence.reconstruct.metadata import _find_first_llm_request_blob_key  # noqa: F401

    def test_generate_session_name_import(self):
        """_generate_session_name must be importable."""
        from context_intelligence.reconstruct.metadata import _generate_session_name  # noqa: F401

    def test_build_subsession_metadata_import(self):
        """_build_subsession_metadata must be importable."""
        from context_intelligence.reconstruct.metadata import _build_subsession_metadata  # noqa: F401

    def test_build_root_metadata_import(self):
        """_build_root_metadata must be importable."""
        from context_intelligence.reconstruct.metadata import _build_root_metadata  # noqa: F401


class TestSubsessionIdRe:
    """_SUBSESSION_ID_RE must match subsession ID patterns."""

    def test_matches_subsession_id(self):
        """Matches 0000000000000000-{child_span}_{agent_name} pattern."""
        from context_intelligence.reconstruct.metadata import _SUBSESSION_ID_RE

        sid = "0000000000000000-abc123def456_foundation_explorer"
        m = _SUBSESSION_ID_RE.match(sid)
        assert m is not None

    def test_captures_child_span(self):
        """Group 1 captures the child_span (hex after the dash)."""
        from context_intelligence.reconstruct.metadata import _SUBSESSION_ID_RE

        sid = "0000000000000000-b22f95d585e24eaa_superpowers-implementer"
        m = _SUBSESSION_ID_RE.match(sid)
        assert m is not None
        assert m.group(1) == "b22f95d585e24eaa"

    def test_captures_agent_name(self):
        """Group 2 captures the agent_name (after the first underscore)."""
        from context_intelligence.reconstruct.metadata import _SUBSESSION_ID_RE

        sid = "0000000000000000-b22f95d585e24eaa_superpowers-implementer"
        m = _SUBSESSION_ID_RE.match(sid)
        assert m is not None
        assert m.group(2) == "superpowers-implementer"

    def test_no_match_for_regular_session_id(self):
        """Does not match a regular (non-subsession) session ID."""
        from context_intelligence.reconstruct.metadata import _SUBSESSION_ID_RE

        sid = "10d123eb-ae1c-40fe-8305-b461a959b521"
        m = _SUBSESSION_ID_RE.match(sid)
        assert m is None

    def test_no_match_for_partial_subsession(self):
        """Does not match an ID with non-zero prefix."""
        from context_intelligence.reconstruct.metadata import _SUBSESSION_ID_RE

        sid = "1111111111111111-abc_agent"
        m = _SUBSESSION_ID_RE.match(sid)
        assert m is None


class TestExtractModelFromConfig:
    """_extract_model_from_config() must extract the priority==0 provider's default_model."""

    def test_returns_model_for_priority_zero_provider(self):
        """Returns default_model for the provider with priority==0."""
        from context_intelligence.reconstruct.metadata import _extract_model_from_config

        config = {
            "providers": [
                {"config": {"priority": 1, "default_model": "gpt-4"}},
                {"config": {"priority": 0, "default_model": "claude-3-5-sonnet-20241022"}},
            ]
        }
        assert _extract_model_from_config(config) == "claude-3-5-sonnet-20241022"

    def test_returns_empty_when_no_providers(self):
        """Returns empty string when providers list is empty."""
        from context_intelligence.reconstruct.metadata import _extract_model_from_config

        assert _extract_model_from_config({}) == ""
        assert _extract_model_from_config({"providers": []}) == ""

    def test_returns_first_provider_model_as_fallback(self):
        """Falls back to first provider when no priority==0 provider found."""
        from context_intelligence.reconstruct.metadata import _extract_model_from_config

        config = {
            "providers": [
                {"config": {"priority": 1, "default_model": "gpt-4-o"}},
                {"config": {"priority": 2, "default_model": "gpt-3.5"}},
            ]
        }
        # Fallback: returns first provider's model
        result = _extract_model_from_config(config)
        assert result == "gpt-4-o"

    def test_handles_non_list_providers(self):
        """Returns empty string when providers is not a list."""
        from context_intelligence.reconstruct.metadata import _extract_model_from_config

        assert _extract_model_from_config({"providers": "not-a-list"}) == ""

    def test_handles_non_dict_provider_entries(self):
        """Skips non-dict provider entries."""
        from context_intelligence.reconstruct.metadata import _extract_model_from_config

        config = {
            "providers": [
                "not-a-dict",
                {"config": {"priority": 0, "default_model": "claude-3"}},
            ]
        }
        assert _extract_model_from_config(config) == "claude-3"

    def test_returns_empty_when_model_missing(self):
        """Returns empty string when priority==0 provider has no default_model."""
        from context_intelligence.reconstruct.metadata import _extract_model_from_config

        config = {
            "providers": [
                {"config": {"priority": 0}},
            ]
        }
        assert _extract_model_from_config(config) == ""


class TestFindSessionStartBlobKey:
    """_find_session_start_blob_key() must find the session_start raw blob key."""

    def test_finds_session_start_key(self):
        """Returns the key containing session_start and ending with __raw."""
        from context_intelligence.reconstruct.metadata import _find_session_start_blob_key

        blob_keys = {
            "sess__llm_request__12345__raw",
            "sess__session_start__67890__raw",
            "sess__session_end__11111__raw",
        }
        result = _find_session_start_blob_key(blob_keys, "sess")
        assert result is not None
        assert "session_start" in result
        assert result.endswith("__raw")

    def test_returns_none_when_no_match(self):
        """Returns None when no session_start blob key found."""
        from context_intelligence.reconstruct.metadata import _find_session_start_blob_key

        blob_keys = {"sess__llm_request__12345__raw", "sess__llm_response__67890__raw"}
        result = _find_session_start_blob_key(blob_keys, "sess")
        assert result is None

    def test_returns_none_for_empty_set(self):
        """Returns None for empty blob_keys set."""
        from context_intelligence.reconstruct.metadata import _find_session_start_blob_key

        result = _find_session_start_blob_key(set(), "sess")
        assert result is None

    def test_requires_raw_suffix(self):
        """Does not match session_start keys without __raw suffix."""
        from context_intelligence.reconstruct.metadata import _find_session_start_blob_key

        blob_keys = {"sess__session_start__67890__processed"}
        result = _find_session_start_blob_key(blob_keys, "sess")
        assert result is None


class TestFindFirstLlmRequestBlobKey:
    """_find_first_llm_request_blob_key() must find the earliest llm_request raw blob key."""

    def test_finds_llm_request_key(self):
        """Returns a key containing llm_request and ending with __raw."""
        from context_intelligence.reconstruct.metadata import _find_first_llm_request_blob_key

        blob_keys = {
            "sess__session_start__12345__raw",
            "sess__llm_request__67890__raw",
            "sess__session_end__99999__raw",
        }
        result = _find_first_llm_request_blob_key(blob_keys)
        assert result is not None
        assert "llm_request" in result

    def test_returns_earliest_key(self):
        """Returns the key with the smallest epoch (alphabetically first)."""
        from context_intelligence.reconstruct.metadata import _find_first_llm_request_blob_key

        blob_keys = {
            "sess__llm_request__99999__raw",
            "sess__llm_request__11111__raw",
            "sess__llm_request__55555__raw",
        }
        result = _find_first_llm_request_blob_key(blob_keys)
        # Should be alphabetically first when sorted
        assert result == "sess__llm_request__11111__raw"

    def test_returns_none_when_no_match(self):
        """Returns None when no llm_request blob key found."""
        from context_intelligence.reconstruct.metadata import _find_first_llm_request_blob_key

        blob_keys = {"sess__session_start__12345__raw", "sess__session_end__99999__raw"}
        result = _find_first_llm_request_blob_key(blob_keys)
        assert result is None

    def test_returns_none_for_empty_set(self):
        """Returns None for empty blob_keys set."""
        from context_intelligence.reconstruct.metadata import _find_first_llm_request_blob_key

        result = _find_first_llm_request_blob_key(set())
        assert result is None

    def test_requires_raw_suffix(self):
        """Does not match llm_request keys without __raw suffix."""
        from context_intelligence.reconstruct.metadata import _find_first_llm_request_blob_key

        blob_keys = {"sess__llm_request__12345__processed"}
        result = _find_first_llm_request_blob_key(blob_keys)
        assert result is None


class TestBuildDiskOnlyMetadata:
    """build_disk_only_metadata() must build metadata from disk files."""

    def test_returns_dict_with_session_id(self):
        """Always returns a dict containing session_id."""
        from context_intelligence.reconstruct.metadata import build_disk_only_metadata

        with tempfile.TemporaryDirectory() as tmpdir:
            result = build_disk_only_metadata("test-session-123", Path(tmpdir))
        assert isinstance(result, dict)
        assert result["session_id"] == "test-session-123"

    def test_reads_ci_metadata_json(self):
        """Reads created timestamp from context-intelligence/metadata.json."""
        from context_intelligence.reconstruct.metadata import build_disk_only_metadata

        with tempfile.TemporaryDirectory() as tmpdir:
            ci_dir = Path(tmpdir) / "context-intelligence"
            ci_dir.mkdir()
            ci_meta = {
                "started_at": "2026-04-10T13:00:00.000+00:00",
                "workspace": "my-workspace",
                "status": "complete",
                "parent_id": "parent-123",
                "working_dir": "/home/user/project",
            }
            (ci_dir / "metadata.json").write_text(json.dumps(ci_meta))
            result = build_disk_only_metadata("sess-abc", Path(tmpdir))

        assert result["created"] == "2026-04-10T13:00:00.000+00:00"
        assert result["workspace"] == "my-workspace"
        assert result["status"] == "complete"
        assert result["parent_id"] == "parent-123"
        assert result["working_dir"] == "/home/user/project"

    def test_fallback_created_from_dir_ctime(self):
        """Falls back to directory ctime when no CI metadata."""
        from context_intelligence.reconstruct.metadata import build_disk_only_metadata

        with tempfile.TemporaryDirectory() as tmpdir:
            result = build_disk_only_metadata("sess-abc", Path(tmpdir))

        assert "created" in result

    def test_counts_turn_count_from_transcript(self):
        """Counts user messages as turn_count from transcript.jsonl."""
        from context_intelligence.reconstruct.metadata import build_disk_only_metadata

        with tempfile.TemporaryDirectory() as tmpdir:
            # Real transcript.jsonl files use compact JSON (no spaces), matching
            # how the prototype writes them via write_jsonl with separators=(",",":")
            transcript_lines = [
                json.dumps({"role": "user", "content": "hello"}, separators=(",", ":")),
                json.dumps({"role": "assistant", "content": "hi"}, separators=(",", ":")),
                json.dumps({"role": "user", "content": "how are you?"}, separators=(",", ":")),
                json.dumps({"role": "assistant", "content": "fine"}, separators=(",", ":")),
            ]
            (Path(tmpdir) / "transcript.jsonl").write_text("\n".join(transcript_lines))
            result = build_disk_only_metadata("sess-abc", Path(tmpdir))

        assert result["turn_count"] == 2

    def test_zero_turn_count_when_no_transcript(self):
        """Returns turn_count=0 when transcript.jsonl does not exist."""
        from context_intelligence.reconstruct.metadata import build_disk_only_metadata

        with tempfile.TemporaryDirectory() as tmpdir:
            result = build_disk_only_metadata("sess-abc", Path(tmpdir))

        assert result["turn_count"] == 0

    def test_incremental_flag_set(self):
        """Sets incremental=True in the returned dict."""
        from context_intelligence.reconstruct.metadata import build_disk_only_metadata

        with tempfile.TemporaryDirectory() as tmpdir:
            result = build_disk_only_metadata("sess-abc", Path(tmpdir))

        assert result["incremental"] is True


class TestGenerateSessionName:
    """_generate_session_name() must generate a name from first prompt_preview."""

    def _make_mock_client(self, cypher_result):
        """Create a mock CIClient that returns cypher_result for any query."""
        mock_client = MagicMock()
        mock_client.cypher.return_value = cypher_result
        return mock_client

    def test_returns_prompt_preview(self):
        """Returns the prompt_preview of the first OrchestratorRun."""
        from context_intelligence.reconstruct.metadata import _generate_session_name

        mock_client = self._make_mock_client([{"r.prompt_preview": "What is the weather today?"}])
        result = _generate_session_name(mock_client, "workspace1", "sess-abc")
        assert result == "What is the weather today?"

    def test_truncates_to_50_chars(self):
        """Truncates prompt_preview to 50 chars with ellipsis."""
        from context_intelligence.reconstruct.metadata import _generate_session_name

        long_preview = "A" * 60  # 60 chars
        mock_client = self._make_mock_client([{"r.prompt_preview": long_preview}])
        result = _generate_session_name(mock_client, "workspace1", "sess-abc")
        assert len(result) == 53  # 50 chars + "..."
        assert result.endswith("...")

    def test_no_truncation_for_short_preview(self):
        """Does not add ellipsis for previews <= 50 chars."""
        from context_intelligence.reconstruct.metadata import _generate_session_name

        short_preview = "Short prompt"
        mock_client = self._make_mock_client([{"r.prompt_preview": short_preview}])
        result = _generate_session_name(mock_client, "workspace1", "sess-abc")
        assert result == "Short prompt"
        assert not result.endswith("...")

    def test_returns_empty_when_no_rows(self):
        """Returns empty string when no OrchestratorRun rows found."""
        from context_intelligence.reconstruct.metadata import _generate_session_name

        mock_client = self._make_mock_client([])
        result = _generate_session_name(mock_client, "workspace1", "sess-abc")
        assert result == ""

    def test_returns_empty_when_preview_is_empty(self):
        """Returns empty string when prompt_preview is empty."""
        from context_intelligence.reconstruct.metadata import _generate_session_name

        mock_client = self._make_mock_client([{"r.prompt_preview": ""}])
        result = _generate_session_name(mock_client, "workspace1", "sess-abc")
        assert result == ""

    def test_returns_empty_when_exception(self):
        """Returns empty string when cypher raises an exception."""
        from context_intelligence.reconstruct.metadata import _generate_session_name

        mock_client = MagicMock()
        mock_client.cypher.side_effect = Exception("connection error")
        result = _generate_session_name(mock_client, "workspace1", "sess-abc")
        assert result == ""


class TestBuildSubsessionMetadata:
    """_build_subsession_metadata() must build minimal metadata for a subsession."""

    def _get_subsession_match(self, session_id):
        """Helper to get a regex match for a subsession ID."""
        from context_intelligence.reconstruct.metadata import _SUBSESSION_ID_RE

        return _SUBSESSION_ID_RE.match(session_id)

    def test_includes_session_id(self):
        """Includes session_id in the metadata."""
        from context_intelligence.reconstruct.metadata import _build_subsession_metadata

        result = _build_subsession_metadata(
            session_id="0000000000000000-abc_foundation_explorer",
            parent_id="parent-123",
            started_at="2026-04-10T13:00:00.000+00:00",
            turn_count=5,
            subsession_match=self._get_subsession_match("0000000000000000-abc_foundation_explorer"),
        )
        assert result["session_id"] == "0000000000000000-abc_foundation_explorer"

    def test_includes_parent_id_when_present(self):
        """Includes parent_id when present."""
        from context_intelligence.reconstruct.metadata import _build_subsession_metadata

        result = _build_subsession_metadata(
            session_id="0000000000000000-abc_foundation_explorer",
            parent_id="parent-123",
            started_at="2026-04-10T13:00:00.000+00:00",
            turn_count=5,
            subsession_match=self._get_subsession_match("0000000000000000-abc_foundation_explorer"),
        )
        assert result["parent_id"] == "parent-123"

    def test_includes_trace_id(self):
        """Includes trace_id (same as parent_id)."""
        from context_intelligence.reconstruct.metadata import _build_subsession_metadata

        result = _build_subsession_metadata(
            session_id="0000000000000000-abc_foundation_explorer",
            parent_id="parent-123",
            started_at="2026-04-10T13:00:00.000+00:00",
            turn_count=5,
            subsession_match=self._get_subsession_match("0000000000000000-abc_foundation_explorer"),
        )
        assert result["trace_id"] == "parent-123"

    def test_includes_agent_name(self):
        """Includes agent_name extracted from session_id."""
        from context_intelligence.reconstruct.metadata import _build_subsession_metadata

        result = _build_subsession_metadata(
            session_id="0000000000000000-abc_foundation_explorer",
            parent_id="parent-123",
            started_at="2026-04-10T13:00:00.000+00:00",
            turn_count=5,
            subsession_match=self._get_subsession_match("0000000000000000-abc_foundation_explorer"),
        )
        # Underscore replaced by colon: foundation_explorer -> foundation:explorer
        assert result["agent_name"] == "foundation:explorer"

    def test_includes_child_span(self):
        """Includes child_span extracted from session_id."""
        from context_intelligence.reconstruct.metadata import _build_subsession_metadata

        result = _build_subsession_metadata(
            session_id="0000000000000000-b22f95d585e24eaa_foundation_explorer",
            parent_id="parent-123",
            started_at="2026-04-10T13:00:00.000+00:00",
            turn_count=5,
            subsession_match=self._get_subsession_match(
                "0000000000000000-b22f95d585e24eaa_foundation_explorer"
            ),
        )
        assert result["child_span"] == "b22f95d585e24eaa"

    def test_includes_turn_count(self):
        """Includes turn_count."""
        from context_intelligence.reconstruct.metadata import _build_subsession_metadata

        result = _build_subsession_metadata(
            session_id="0000000000000000-abc_foundation_explorer",
            parent_id="parent-123",
            started_at="2026-04-10T13:00:00.000+00:00",
            turn_count=7,
            subsession_match=self._get_subsession_match("0000000000000000-abc_foundation_explorer"),
        )
        assert result["turn_count"] == 7

    def test_includes_created_when_started_at_present(self):
        """Includes created timestamp from started_at."""
        from context_intelligence.reconstruct.metadata import _build_subsession_metadata

        result = _build_subsession_metadata(
            session_id="0000000000000000-abc_foundation_explorer",
            parent_id="parent-123",
            started_at="2026-04-10T13:00:00.000+00:00",
            turn_count=5,
            subsession_match=self._get_subsession_match("0000000000000000-abc_foundation_explorer"),
        )
        assert result["created"] == "2026-04-10T13:00:00.000+00:00"

    def test_no_subsession_match_uses_parent_id_only(self):
        """When subsession_match is None, only includes parent_id-based fields."""
        from context_intelligence.reconstruct.metadata import _build_subsession_metadata

        result = _build_subsession_metadata(
            session_id="some-non-subsession-id",
            parent_id="parent-123",
            started_at="2026-04-10T13:00:00.000+00:00",
            turn_count=3,
            subsession_match=None,
        )
        assert result["session_id"] == "some-non-subsession-id"
        assert result["parent_id"] == "parent-123"
        assert "child_span" not in result
        # agent_name should not be present when no subsession match
        assert "agent_name" not in result


class TestBuildRootMetadata:
    """_build_root_metadata() must resolve session_start blob with fallback chain."""

    def _make_mock_client(self, blob_keys=None, session_start_blob=None, llm_request_blob=None):
        """Create a mock CIClient."""
        mock_client = MagicMock()
        mock_client.list_blob_keys.return_value = blob_keys or set()

        def fetch_blob_side_effect(session_id, key):
            if session_start_blob is not None and "session_start" in key:
                return session_start_blob
            if llm_request_blob is not None and "llm_request" in key:
                return llm_request_blob
            return None

        mock_client.fetch_blob.side_effect = fetch_blob_side_effect
        return mock_client

    def test_returns_dict_with_session_id(self):
        """Always returns a dict with session_id."""
        from context_intelligence.reconstruct.metadata import _build_root_metadata

        mock_client = self._make_mock_client()
        result = _build_root_metadata(
            client=mock_client,
            session_id="sess-abc",
            started_at="2026-04-10T13:00:00.000+00:00",
            turn_count=3,
            session_data={},
        )
        assert result["session_id"] == "sess-abc"

    def test_includes_bundle_from_session_start_blob(self):
        """Includes bundle from session_start blob with 'bundle:' prefix."""
        from context_intelligence.reconstruct.metadata import _build_root_metadata

        session_start_blob = {
            "bundle_name": "context-intelligence",
            "working_dir": "/home/user/project",
            "providers": [{"config": {"priority": 0, "default_model": "claude-3-5-sonnet"}}],
        }
        mock_client = self._make_mock_client(
            blob_keys={"sess__session_start__12345__raw"},
            session_start_blob=session_start_blob,
        )
        result = _build_root_metadata(
            client=mock_client,
            session_id="sess-abc",
            started_at="2026-04-10T13:00:00.000+00:00",
            turn_count=3,
            session_data={},
        )
        assert result["bundle"] == "bundle:context-intelligence"

    def test_bundle_prefix_not_duplicated(self):
        """Does not duplicate 'bundle:' prefix if already present."""
        from context_intelligence.reconstruct.metadata import _build_root_metadata

        session_start_blob = {"bundle_name": "bundle:context-intelligence"}
        mock_client = self._make_mock_client(
            blob_keys={"sess__session_start__12345__raw"},
            session_start_blob=session_start_blob,
        )
        result = _build_root_metadata(
            client=mock_client,
            session_id="sess-abc",
            started_at="",
            turn_count=0,
            session_data={},
        )
        assert result["bundle"] == "bundle:context-intelligence"

    def test_includes_model_from_session_start_blob(self):
        """Includes model from session_start blob via _extract_model_from_config."""
        from context_intelligence.reconstruct.metadata import _build_root_metadata

        session_start_blob = {
            "providers": [{"config": {"priority": 0, "default_model": "claude-3-5-sonnet"}}],
        }
        mock_client = self._make_mock_client(
            blob_keys={"sess__session_start__12345__raw"},
            session_start_blob=session_start_blob,
        )
        result = _build_root_metadata(
            client=mock_client,
            session_id="sess-abc",
            started_at="",
            turn_count=0,
            session_data={},
        )
        assert result["model"] == "claude-3-5-sonnet"

    def test_includes_model_from_llm_request_fallback(self):
        """Falls back to llm_request blob for model when no session_start blob."""
        from context_intelligence.reconstruct.metadata import _build_root_metadata

        llm_blob = {"model": "gpt-4o"}
        mock_client = self._make_mock_client(
            blob_keys={"sess__llm_request__12345__raw"},
            llm_request_blob=llm_blob,
        )
        result = _build_root_metadata(
            client=mock_client,
            session_id="sess-abc",
            started_at="",
            turn_count=0,
            session_data={},
        )
        assert result.get("model") == "gpt-4o"

    def test_includes_turn_count(self):
        """Includes turn_count in the result."""
        from context_intelligence.reconstruct.metadata import _build_root_metadata

        mock_client = self._make_mock_client()
        result = _build_root_metadata(
            client=mock_client,
            session_id="sess-abc",
            started_at="",
            turn_count=42,
            session_data={},
        )
        assert result["turn_count"] == 42

    def test_includes_incremental_true(self):
        """Includes incremental=True."""
        from context_intelligence.reconstruct.metadata import _build_root_metadata

        mock_client = self._make_mock_client()
        result = _build_root_metadata(
            client=mock_client,
            session_id="sess-abc",
            started_at="",
            turn_count=0,
            session_data={},
        )
        assert result["incremental"] is True

    def test_handles_exception_gracefully(self):
        """Gracefully handles exceptions when resolving blobs."""
        from context_intelligence.reconstruct.metadata import _build_root_metadata

        mock_client = MagicMock()
        mock_client.list_blob_keys.side_effect = Exception("network error")
        result = _build_root_metadata(
            client=mock_client,
            session_id="sess-abc",
            started_at="",
            turn_count=0,
            session_data={},
        )
        # Should return a dict even on exception
        assert isinstance(result, dict)
        assert result["session_id"] == "sess-abc"


class TestExtractMetadata:
    """extract_metadata() must query Session node and build proper metadata."""

    def _make_mock_client(
        self,
        session_rows=None,
        run_rows=None,
        session_name_rows=None,
        blob_keys=None,
        session_start_blob=None,
    ):
        """Create a mock CIClient with configured cypher responses."""
        mock_client = MagicMock()

        def cypher_side_effect(query, workspace="*"):
            if (
                "Session" in query
                and "node_id" in query
                and "turn_count" not in query
                and "OrchestratorRun" not in query
            ):
                return session_rows if session_rows is not None else []
            if "OrchestratorRun" in query and "count" in query:
                return run_rows if run_rows is not None else [{"turn_count": 0}]
            if "OrchestratorRun" in query and "prompt_preview" in query:
                return session_name_rows if session_name_rows is not None else []
            return []

        mock_client.cypher.side_effect = cypher_side_effect
        mock_client.list_blob_keys.return_value = blob_keys or set()
        if session_start_blob:
            mock_client.fetch_blob.return_value = session_start_blob
        else:
            mock_client.fetch_blob.return_value = None
        return mock_client

    def test_returns_none_when_session_not_found(self):
        """Returns None when no Session node found."""
        from context_intelligence.reconstruct.metadata import extract_metadata

        mock_client = self._make_mock_client(session_rows=[])
        result = extract_metadata(mock_client, "workspace1", "sess-abc")
        assert result is None

    def test_returns_dict_for_root_session(self):
        """Returns a dict for a root session."""
        from context_intelligence.reconstruct.metadata import extract_metadata

        session_rows = [
            {
                "s.node_id": "sess-abc",
                "s.started_at": "2026-04-10T13:00:00.000+00:00",
                "s.ended_at": None,
                "s.status": "active",
                "s.data": json.dumps({}),
            }
        ]
        mock_client = self._make_mock_client(session_rows=session_rows)
        result = extract_metadata(mock_client, "workspace1", "sess-abc")
        assert isinstance(result, dict)
        assert result["session_id"] == "sess-abc"

    def test_detects_subsession_by_id_pattern(self):
        """Detects subsession by the 0000000000000000- prefix in session_id."""
        from context_intelligence.reconstruct.metadata import extract_metadata

        subsession_id = "0000000000000000-b22f95d585e24eaa_foundation_explorer"
        session_rows = [
            {
                "s.node_id": subsession_id,
                "s.started_at": "2026-04-10T13:00:00.000+00:00",
                "s.ended_at": None,
                "s.status": "active",
                "s.data": json.dumps({"parent_id": "root-sess-abc"}),
            }
        ]
        mock_client = self._make_mock_client(session_rows=session_rows)
        result = extract_metadata(mock_client, "workspace1", subsession_id)
        assert result is not None
        # Should have agent_name and child_span
        assert "agent_name" in result
        assert "child_span" in result

    def test_detects_subsession_by_parent_id(self):
        """Detects subsession when Session.data contains parent_id."""
        from context_intelligence.reconstruct.metadata import extract_metadata

        session_rows = [
            {
                "s.node_id": "some-regular-id",
                "s.started_at": "2026-04-10T13:00:00.000+00:00",
                "s.ended_at": None,
                "s.status": "active",
                "s.data": json.dumps({"parent_id": "root-sess-abc"}),
            }
        ]
        mock_client = self._make_mock_client(session_rows=session_rows)
        result = extract_metadata(mock_client, "workspace1", "some-regular-id")
        assert result is not None
        assert result["parent_id"] == "root-sess-abc"

    def test_turn_count_from_orchestrator_run_count(self):
        """Counts OrchestratorRun nodes for turn_count."""
        from context_intelligence.reconstruct.metadata import extract_metadata

        session_rows = [
            {
                "s.node_id": "sess-abc",
                "s.started_at": "2026-04-10T13:00:00.000+00:00",
                "s.ended_at": None,
                "s.status": "active",
                "s.data": json.dumps({}),
            }
        ]
        run_rows = [{"turn_count": 7}]
        mock_client = self._make_mock_client(session_rows=session_rows, run_rows=run_rows)
        result = extract_metadata(mock_client, "workspace1", "sess-abc")
        assert result is not None
        assert result["turn_count"] == 7

    def test_generates_session_name_when_missing(self):
        """Generates session name from first prompt_preview when name is absent."""
        from context_intelligence.reconstruct.metadata import extract_metadata

        session_rows = [
            {
                "s.node_id": "sess-abc",
                "s.started_at": "2026-04-10T13:00:00.000+00:00",
                "s.ended_at": None,
                "s.status": "active",
                "s.data": json.dumps({}),
            }
        ]
        session_name_rows = [{"r.prompt_preview": "Help me debug this code"}]
        mock_client = self._make_mock_client(
            session_rows=session_rows, session_name_rows=session_name_rows
        )
        result = extract_metadata(mock_client, "workspace1", "sess-abc")
        assert result is not None
        assert result.get("name") == "Help me debug this code"

    def test_passes_workspace_to_cypher(self):
        """Passes workspace to all cypher calls."""
        from context_intelligence.reconstruct.metadata import extract_metadata

        session_rows = [
            {
                "s.node_id": "sess-abc",
                "s.started_at": "",
                "s.ended_at": None,
                "s.status": "active",
                "s.data": json.dumps({}),
            }
        ]
        mock_client = self._make_mock_client(session_rows=session_rows)
        extract_metadata(mock_client, "my-workspace", "sess-abc")

        for call in mock_client.cypher.call_args_list:
            assert "my-workspace" in str(call)


# ---------------------------------------------------------------------------
# Risk 2: reconstruct metadata must SURFACE a genuine blob-store failure, not
# silently emit partial metadata that looks complete.
# ---------------------------------------------------------------------------


class TestBuildRootMetadataFailLoud:
    """_build_root_metadata() propagates CIClientError from list_blob_keys()
    instead of swallowing it to partial (enrichment-less) metadata."""

    def test_ciclienterror_from_list_blob_keys_propagates(self):
        import pytest

        from context_intelligence.client import CIClientError
        from context_intelligence.reconstruct.metadata import _build_root_metadata

        client = MagicMock()
        client.list_blob_keys.side_effect = CIClientError(
            "server down", error_type="connection_error", url="http://x/blobs/s1"
        )

        # A genuine transport failure must NOT be swallowed into "no enrichment".
        with pytest.raises(CIClientError) as excinfo:
            _build_root_metadata(
                client=client,
                session_id="s1",
                started_at="2024-01-01T00:00:00Z",
                turn_count=3,
                session_data={},
            )
        assert excinfo.value.error_type == "connection_error"
        # fetch_blob must never be reached once listing already failed loud.
        client.fetch_blob.assert_not_called()

    def test_http_status_from_list_blob_keys_propagates(self):
        import pytest

        from context_intelligence.client import CIClientError
        from context_intelligence.reconstruct.metadata import _build_root_metadata

        client = MagicMock()
        client.list_blob_keys.side_effect = CIClientError(
            "boom", error_type="http_status", url="http://x/blobs/s1", status_code=500
        )
        with pytest.raises(CIClientError) as excinfo:
            _build_root_metadata(
                client=client,
                session_id="s1",
                started_at="2024-01-01T00:00:00Z",
                turn_count=1,
                session_data={},
            )
        assert excinfo.value.error_type == "http_status"

    def test_genuine_empty_blob_keys_still_builds_metadata(self):
        """A session with NO blobs (genuine empty) is not an error: metadata is
        built without enrichment fields, no exception."""
        from context_intelligence.reconstruct.metadata import _build_root_metadata

        client = MagicMock()
        client.list_blob_keys.return_value = set()  # legitimately empty

        metadata = _build_root_metadata(
            client=client,
            session_id="s1",
            started_at="2024-01-01T00:00:00Z",
            turn_count=5,
            session_data={},
        )
        assert metadata["session_id"] == "s1"
        assert metadata["turn_count"] == 5
        # No enrichment blobs -> those fields are simply absent (not fabricated).
        assert "bundle" not in metadata
        assert "model" not in metadata
        client.fetch_blob.assert_not_called()
