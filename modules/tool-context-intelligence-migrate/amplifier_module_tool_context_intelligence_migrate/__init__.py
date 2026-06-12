"""context-intelligence-migrate — migrate legacy hooks-logging session events to CI format."""

from amplifier_module_tool_context_intelligence_migrate.classify import (
    SessionInfo,
    bucket_session,
    is_live_session,
    scan_projects,
)
from amplifier_module_tool_context_intelligence_migrate.transform import (
    SchemaVersionError,
    is_content_superset,
    transform_session,
)

__all__ = [
    "SchemaVersionError",
    "SessionInfo",
    "bucket_session",
    "is_content_superset",
    "is_live_session",
    "scan_projects",
    "transform_session",
]
