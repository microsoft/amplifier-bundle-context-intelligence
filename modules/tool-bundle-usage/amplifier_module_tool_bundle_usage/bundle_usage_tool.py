"""BundleUsageTool — agent-facing tool for bundle usage analysis.

Resolves configuration lazily at execute() time via the
``context_intelligence.config_resolver`` coordinator capability, constructs
an ``AsyncCIClient`` from the resolved values, and delegates to
``context_intelligence.bundle_analysis.run_bundle_analysis``.
"""

from __future__ import annotations

from typing import Any

from amplifier_core.models import ToolResult
from context_intelligence.bundle_analysis import run_bundle_analysis
from context_intelligence.client import AsyncCIClient


class BundleUsageTool:
    """Run bundle usage analysis (signals + inventory + gap) for a session or workspace.

    Implements the Amplifier Tool protocol (``name``, ``description``,
    ``input_schema``, ``execute``).
    """

    def __init__(self, coordinator: Any) -> None:
        self._coordinator = coordinator
        self._resolver: Any | None = None

    @property
    def name(self) -> str:
        return "bundle_usage"

    @property
    def description(self) -> str:
        return (
            "Analyse what bundles and components a session or workspace used "
            "(via Cypher signals against the context-intelligence graph) vs "
            "what each bundle declares (via bundle cache scan). Returns "
            "signals, declared inventory, per-bundle gap, and improvement "
            "classifications (tree-shake / mode-refactor / config-gap)."
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
        if self._resolver is None:
            self._resolver = self._coordinator.get_capability(
                "context_intelligence.config_resolver"
            )

        if self._resolver is None:
            return ToolResult(
                success=False,
                error={
                    "message": "context-intelligence hook not configured",
                    "type": "configuration_error",
                },
            )

        server_url = self._resolver.context_intelligence_server_url
        if not server_url:
            return ToolResult(
                success=False,
                error={
                    "message": "context-intelligence server URL not configured",
                    "type": "configuration_error",
                },
            )

        api_key = self._resolver.context_intelligence_api_key or ""
        resolver_ws = self._resolver.workspace
        ws_override = input.get("workspace")
        effective_workspace = ws_override if ws_override is not None else resolver_ws

        client = AsyncCIClient(server_url=server_url, api_key=api_key)
        try:
            result = await run_bundle_analysis(
                client=client,
                workspace=effective_workspace,
                session_id=input.get("session_id"),
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                error={"message": str(exc), "type": "analysis_error"},
            )

        return ToolResult(success=True, output=result)
