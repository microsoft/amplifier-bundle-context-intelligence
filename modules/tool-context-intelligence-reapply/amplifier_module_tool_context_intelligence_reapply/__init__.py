"""Same-session ingestion-reapply tool.

Exposes the root session's ``context_intelligence.reapply_ingestion`` capability
(registered by the ingestion hook) as an agent-callable tool, so the ROOT agent
can make a mid-session destination `exclude` edit take effect immediately -- no
restart -- against the already-running session's fan-out.

Only meaningful in a session that actually mounts the ingestion hook (i.e. the
ROOT session via behaviors/context-intelligence-logging.yaml). A delegated agent
that does not mount the hook has no such capability on its own coordinator; the
tool then fails loud rather than silently doing nothing -- which is exactly the
negative-control boundary (a delegate cannot reach the root's live hook state).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from amplifier_core.models import ToolResult

__amplifier_module_type__ = "tool"
__all__ = ["mount"]

_CAP = "context_intelligence.reapply_ingestion"
_VERIFY_CAP = "context_intelligence.verify_ingestion_consistency"
_DEFAULT_SETTINGS = str(Path("~/.amplifier/settings.yaml").expanduser())


class ReapplyIngestionTool:
    """Re-apply the ingestion hook's per-destination include/exclude live."""

    def __init__(self, coordinator: Any) -> None:
        self._coordinator = coordinator

    @property
    def name(self) -> str:
        return "reapply_ingestion"

    @property
    def description(self) -> str:
        return (
            "Make a Context Intelligence ingestion destination `exclude` change take "
            "effect IMMEDIATELY in the CURRENTLY-RUNNING session, without a restart. "
            "Call this AFTER the exclude edit has been written to settings.yaml. Re-reads "
            "the destinations block from settings.yaml, re-evaluates routing for this "
            "session's working directory, and swaps the live per-destination dispatchers "
            "(drain-safe). Returns the new active destinations and their include/exclude, "
            "and fails loud if the live filter does not match what is on disk."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "settings_path": {
                    "type": "string",
                    "description": (
                        "Path to the settings.yaml carrying the destinations block. "
                        f"Defaults to {_DEFAULT_SETTINGS}."
                    ),
                },
                "verify_only": {
                    "type": "boolean",
                    "description": (
                        "If true, do NOT reapply -- only fail-loud compare the session's "
                        "LIVE exclude filter against settings.yaml on disk (both directions)."
                    ),
                },
            },
        }

    async def execute(self, input: dict[str, Any]) -> ToolResult:  # noqa: A002
        settings_path = input.get("settings_path") or _DEFAULT_SETTINGS
        verify_only = bool(input.get("verify_only"))

        if verify_only:
            verify = self._coordinator.get_capability(_VERIFY_CAP)
            if verify is None:
                return ToolResult(
                    success=False,
                    output={"error": f"capability {_VERIFY_CAP} unavailable in this session "
                            "(no ingestion hook mounted here) -- cannot verify."},
                )
            try:
                result = verify(settings_path)
            except Exception as exc:  # noqa: BLE001 - surface the fail-loud reason to the agent
                return ToolResult(success=False, output={"error": str(exc)})
            return ToolResult(success=True, output=result)

        reapply = self._coordinator.get_capability(_CAP)
        if reapply is None:
            return ToolResult(
                success=False,
                output={"error": f"capability {_CAP} unavailable in this session (no "
                        "ingestion hook mounted here) -- this session's fan-out was NOT "
                        "changed. The reapply tool only affects the session that mounts "
                        "the ingestion hook (the root session)."},
            )
        try:
            report = await reapply(settings_path=settings_path, verify_disk=True)
        except Exception as exc:  # noqa: BLE001 - surface the fail-loud reason to the agent
            return ToolResult(success=False, output={"error": str(exc)})
        return ToolResult(success=True, output=report)


async def mount(coordinator: Any, config: Any) -> None:
    tool = ReapplyIngestionTool(coordinator)
    await coordinator.mount("tools", tool, name=tool.name)
