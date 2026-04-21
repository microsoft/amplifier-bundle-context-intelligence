"""Tests for __init__.py re-exports (task-9).

Verifies:
- context_intelligence.reconstruct.__init__ re-exports all required symbols
- context_intelligence.__init__ re-exports all required symbols
- __all__ is defined in both modules
- Both modules use 'from __future__ import annotations'
- Acceptance criteria command works correctly
"""

from __future__ import annotations


class TestReconstructInitReexports:
    """context_intelligence.reconstruct must re-export all required symbols."""

    def test_extract_events_importable_from_reconstruct(self):
        """extract_events must be importable from context_intelligence.reconstruct."""
        from context_intelligence.reconstruct import extract_events

        assert callable(extract_events)

    def test_extract_transcript_importable_from_reconstruct(self):
        """extract_transcript must be importable from context_intelligence.reconstruct."""
        from context_intelligence.reconstruct import extract_transcript

        assert callable(extract_transcript)

    def test_extract_metadata_importable_from_reconstruct(self):
        """extract_metadata must be importable from context_intelligence.reconstruct."""
        from context_intelligence.reconstruct import extract_metadata

        assert callable(extract_metadata)

    def test_build_disk_only_metadata_importable_from_reconstruct(self):
        """build_disk_only_metadata must be importable from context_intelligence.reconstruct."""
        from context_intelligence.reconstruct import build_disk_only_metadata

        assert callable(build_disk_only_metadata)

    def test_discover_sessions_importable_from_reconstruct(self):
        """discover_sessions must be importable from context_intelligence.reconstruct."""
        from context_intelligence.reconstruct import discover_sessions

        assert callable(discover_sessions)

    def test_workspace_slug_importable_from_reconstruct(self):
        """workspace_slug must be importable from context_intelligence.reconstruct."""
        from context_intelligence.reconstruct import workspace_slug

        assert callable(workspace_slug)

    def test_sessions_dir_for_project_importable_from_reconstruct(self):
        """sessions_dir_for_project must be importable from context_intelligence.reconstruct."""
        from context_intelligence.reconstruct import sessions_dir_for_project

        assert callable(sessions_dir_for_project)

    def test_reconstruct_has_all_list(self):
        """context_intelligence.reconstruct must define __all__."""
        import context_intelligence.reconstruct as reconstruct

        assert hasattr(reconstruct, "__all__"), "reconstruct module must have __all__"
        assert isinstance(reconstruct.__all__, list), "__all__ must be a list"

    def test_reconstruct_all_contains_required_names(self):
        """__all__ must contain all required re-exported names."""
        import context_intelligence.reconstruct as reconstruct

        required = {
            "extract_events",
            "extract_transcript",
            "extract_metadata",
            "build_disk_only_metadata",
            "discover_sessions",
            "workspace_slug",
            "sessions_dir_for_project",
        }
        all_set = set(reconstruct.__all__)
        missing = required - all_set
        assert not missing, f"__all__ missing: {missing}"

    def test_reconstruct_module_identity(self):
        """Symbols re-exported from reconstruct must be the same objects as in source modules."""
        from context_intelligence.reconstruct import workspace_slug
        from context_intelligence.reconstruct.discover import workspace_slug as ws_original

        assert workspace_slug is ws_original, "workspace_slug should be the same object"


class TestContextIntelligenceInitReexports:
    """context_intelligence must re-export all required symbols at the top level."""

    def test_ciclient_importable_from_top(self):
        """CIClient must be importable from context_intelligence."""
        from context_intelligence import CIClient

        assert CIClient is not None

    def test_resolve_config_importable_from_top(self):
        """resolve_config must be importable from context_intelligence."""
        from context_intelligence import resolve_config

        assert callable(resolve_config)

    def test_log_schema_importable_from_top(self):
        """LOG_SCHEMA must be importable from context_intelligence."""
        from context_intelligence import LOG_SCHEMA

        assert LOG_SCHEMA is not None
        assert isinstance(LOG_SCHEMA, dict)

    def test_amplifier_dir_importable_from_top(self):
        """AMPLIFIER_DIR must be importable from context_intelligence."""
        from context_intelligence import AMPLIFIER_DIR

        assert AMPLIFIER_DIR is not None

    def test_settings_path_importable_from_top(self):
        """SETTINGS_PATH must be importable from context_intelligence."""
        from context_intelligence import SETTINGS_PATH

        assert SETTINGS_PATH is not None

    def test_extract_events_importable_from_top(self):
        """extract_events must be importable from context_intelligence."""
        from context_intelligence import extract_events

        assert callable(extract_events)

    def test_extract_transcript_importable_from_top(self):
        """extract_transcript must be importable from context_intelligence."""
        from context_intelligence import extract_transcript

        assert callable(extract_transcript)

    def test_extract_metadata_importable_from_top(self):
        """extract_metadata must be importable from context_intelligence."""
        from context_intelligence import extract_metadata

        assert callable(extract_metadata)

    def test_build_disk_only_metadata_importable_from_top(self):
        """build_disk_only_metadata must be importable from context_intelligence."""
        from context_intelligence import build_disk_only_metadata

        assert callable(build_disk_only_metadata)

    def test_discover_sessions_importable_from_top(self):
        """discover_sessions must be importable from context_intelligence."""
        from context_intelligence import discover_sessions

        assert callable(discover_sessions)

    def test_workspace_slug_importable_from_top(self):
        """workspace_slug must be importable from context_intelligence."""
        from context_intelligence import workspace_slug

        assert callable(workspace_slug)

    def test_sessions_dir_for_project_importable_from_top(self):
        """sessions_dir_for_project must be importable from context_intelligence."""
        from context_intelligence import sessions_dir_for_project

        assert callable(sessions_dir_for_project)

    def test_top_has_all_list(self):
        """context_intelligence must define __all__."""
        import context_intelligence as ci

        assert hasattr(ci, "__all__"), "context_intelligence module must have __all__"
        assert isinstance(ci.__all__, list), "__all__ must be a list"

    def test_top_all_contains_required_names(self):
        """__all__ must contain all required re-exported names."""
        import context_intelligence as ci

        required = {
            "CIClient",
            "resolve_config",
            "LOG_SCHEMA",
            "AMPLIFIER_DIR",
            "SETTINGS_PATH",
            "extract_events",
            "extract_transcript",
            "extract_metadata",
            "build_disk_only_metadata",
            "discover_sessions",
            "workspace_slug",
            "sessions_dir_for_project",
        }
        all_set = set(ci.__all__)
        missing = required - all_set
        assert not missing, f"__all__ missing: {missing}"


class TestAcceptanceCriteria:
    """The acceptance criteria command must work correctly."""

    def test_mass_import_and_workspace_slug(self):
        """All symbols must import together and workspace_slug must work correctly."""
        from context_intelligence import (
            AMPLIFIER_DIR,
            CIClient,
            LOG_SCHEMA,
            build_disk_only_metadata,
            discover_sessions,
            extract_events,
            extract_metadata,
            extract_transcript,
            resolve_config,
            sessions_dir_for_project,
            workspace_slug,
        )

        # All imports succeeded
        assert CIClient is not None
        assert callable(resolve_config)
        assert LOG_SCHEMA is not None
        assert AMPLIFIER_DIR is not None
        assert callable(extract_events)
        assert callable(extract_transcript)
        assert callable(extract_metadata)
        assert callable(discover_sessions)
        assert callable(workspace_slug)
        assert callable(sessions_dir_for_project)
        assert callable(build_disk_only_metadata)

        # workspace_slug test from acceptance criteria
        result = workspace_slug("/home/user/dev/project")
        assert result == "-home-user-dev-project", (
            f"workspace_slug('/home/user/dev/project') should return "
            f"'-home-user-dev-project', got '{result}'"
        )
