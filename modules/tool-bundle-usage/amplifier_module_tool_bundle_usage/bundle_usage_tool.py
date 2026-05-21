"""BundleUsageTool — agent-facing tool for bundle usage analysis.

Resolves configuration lazily at execute() time via the
``context_intelligence.config_resolver`` coordinator capability and delegates
to ``context_intelligence.bundle_analysis.run_bundle_analysis``.
No external CI server is required — analysis runs entirely from local JSONL
session files and the bundle cache on disk.
"""

from __future__ import annotations

from typing import Any

from amplifier_core.models import ToolResult
from context_intelligence.bundle_analysis import run_bundle_analysis


class BundleUsageTool:
    """Run bundle usage analysis (signals + inventory + gap) for a session or workspace.

    Implements the Amplifier Tool protocol (``name``, ``description``,
    ``input_schema``, ``execute``).
    """

    def __init__(self, coordinator: Any) -> None:
        self._coordinator = coordinator
        self._resolver: Any = coordinator._resolver

    @property
    def name(self) -> str:
        return "bundle_usage"

    @property
    def description(self) -> str:
        return (
            "Analyse what bundles and components a session or workspace used "
            "(via JSONL session events) vs what each bundle declares (via bundle cache scan). "
            "Returns signals, declared inventory, per-bundle gap, and improvement "
            "classifications (tree-shake / mode-refactor / config-gap / mode-never-activated)."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": (
                        "Optional session ID to scope signals. When omitted, "
                        "the analysis aggregates across the workspace."
                    ),
                },
                "workspace": {
                    "type": "string",
                    "description": (
                        "Optional workspace override. Defaults to the workspace "
                        "configured on the context-intelligence config resolver."
                    ),
                },
            },
            "required": [],
        }

    async def execute(self, input: dict[str, Any]) -> ToolResult:  # noqa: A002
        resolver_ws = self._resolver.workspace
        ws_override = input.get("workspace")
        effective_workspace = ws_override if ws_override is not None else resolver_ws

        try:
            result = await run_bundle_analysis(
                workspace=effective_workspace,
                session_id=input.get("session_id"),
                base_path=self._resolver.base_path,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                success=False,
                error={"message": str(exc), "type": "analysis_error"},
            )
        return ToolResult(success=True, output=result)
