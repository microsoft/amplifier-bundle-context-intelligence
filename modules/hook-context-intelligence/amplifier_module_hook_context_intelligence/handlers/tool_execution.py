"""ToolExecutionHandler — owns :ToolExecution lifecycle events."""

from __future__ import annotations

import logging
from typing import Any

from amplifier_core.models import HookResult

from ..services import HookStateService
from ..utils import EventLogContext, HandlerLogger, make_node_id

logger = logging.getLogger(__name__)

RESULT_PREVIEW_MAX_LEN = 500


class ToolExecutionHandler:
    handled_events: frozenset[str] = frozenset(
        {
            "tool:pre",
            "tool:post",
            "tool:error",
            "delegate:agent_spawned",
            "delegate:agent_completed",
            "delegate:context_inherited",
            "delegate:session_resumed",
        }
    )

    def __init__(self, services: HookStateService) -> None:
        self.services = services
        self._log = HandlerLogger("ToolExecutionHandler", logger)

    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        log = self._log.with_event(event, data)

        if event == "tool:pre":
            return await self._handle_tool_pre(data, log)
        if event == "tool:post":
            return await self._handle_tool_post(data, log)
        if event == "tool:error":
            return await self._handle_tool_error(data, log)
        if event == "delegate:agent_spawned":
            return await self._handle_delegate_agent_spawned(data, log)
        if event == "delegate:agent_completed":
            return await self._handle_delegate_agent_completed(data, log)

        # delegate:context_inherited, delegate:session_resumed — no-op for v1
        return HookResult(action="continue")

    async def _handle_tool_pre(self, data: dict[str, Any], log: EventLogContext) -> HookResult:
        session_id = data.get("session_id")
        if not session_id:
            log.error("received event without session_id")
            return HookResult(action="continue")

        timestamp = data.get("timestamp", "")
        cursors = self.services.get_cursors(session_id)

        # Generate deterministic TE ID
        te_id = make_node_id(session_id, "tool:pre", timestamp)

        # Build ToolExecution node properties
        properties: dict[str, Any] = {
            "tool_call_id": data.get("tool_call_id", ""),
            "tool_name": data.get("tool_name", ""),
            "parallel_group_id": data.get("parallel_group_id", ""),
            "started_at": timestamp,
            "status": "executing",
            "session_id": session_id,
        }

        # Create ToolExecution node
        await self.services.graph.upsert_node(te_id, {"ToolExecution"}, properties)

        # Create TRIGGERED edge: current_step → ToolExecution (only if step exists)
        step_id = cursors.current_step_id
        if step_id:
            await self.services.graph.upsert_edge(
                step_id,
                te_id,
                "TRIGGERED",
                {"seq": cursors.step_counter, "occurred_at": timestamp},
            )

        # PARALLEL_WITH: link new TE to each existing TE in same parallel_group_id
        parallel_group_id = data.get("parallel_group_id", "")
        if parallel_group_id:
            existing_tes = cursors.parallel_groups.get(parallel_group_id, [])
            for existing_te_id in existing_tes:
                await self.services.graph.upsert_edge(
                    te_id,
                    existing_te_id,
                    "PARALLEL_WITH",
                    {"occurred_at": timestamp},
                )

            # Add this TE to the parallel group
            if parallel_group_id not in cursors.parallel_groups:
                cursors.parallel_groups[parallel_group_id] = []
            cursors.parallel_groups[parallel_group_id].append(te_id)

        # Populate tool_call_map
        tool_call_id = data.get("tool_call_id", "")
        if tool_call_id:
            cursors.tool_call_map[tool_call_id] = te_id

        log.info("Created ToolExecution node %s", te_id)

        return HookResult(action="continue")

    async def _handle_tool_post(self, data: dict[str, Any], log: EventLogContext) -> HookResult:
        session_id = data.get("session_id")
        if not session_id:
            log.error("received event without session_id")
            return HookResult(action="continue")

        cursors = self.services.get_cursors(session_id)
        tool_call_id = data.get("tool_call_id", "")
        te_id = cursors.tool_call_map.get(tool_call_id)
        if not te_id:
            log.warning("No TE node for tool_call_id %s", tool_call_id)
            return HookResult(action="continue")

        timestamp = data.get("timestamp", "")
        result = data.get("result", "")
        result_str = str(result) if result is not None else ""
        result_preview = result_str[:RESULT_PREVIEW_MAX_LEN]

        properties: dict[str, Any] = {
            "status": "complete",
            "ended_at": timestamp,
            "result_preview": result_preview,
        }

        await self.services.graph.upsert_node(te_id, set(), properties)
        log.info("Completed ToolExecution %s", te_id)

        return HookResult(action="continue")

    async def _handle_tool_error(self, data: dict[str, Any], log: EventLogContext) -> HookResult:
        session_id = data.get("session_id")
        if not session_id:
            log.error("received event without session_id")
            return HookResult(action="continue")

        cursors = self.services.get_cursors(session_id)
        tool_call_id = data.get("tool_call_id", "")
        te_id = cursors.tool_call_map.get(tool_call_id)
        if not te_id:
            log.warning("No TE node for tool_call_id %s", tool_call_id)
            return HookResult(action="continue")

        timestamp = data.get("timestamp", "")
        error_message = data.get("error", "")

        properties: dict[str, Any] = {
            "status": "error",
            "ended_at": timestamp,
            "error": error_message,
        }

        await self.services.graph.upsert_node(te_id, set(), properties)
        log.info("Errored ToolExecution %s", te_id)

        return HookResult(action="continue")

    async def _handle_delegate_agent_spawned(
        self, data: dict[str, Any], log: EventLogContext
    ) -> HookResult:
        session_id = data.get("session_id")
        if not session_id:
            log.error("received event without session_id")
            return HookResult(action="continue")

        cursors = self.services.get_cursors(session_id)
        tool_call_id = data.get("tool_call_id", "")
        te_id = cursors.tool_call_map.get(tool_call_id)
        if not te_id:
            log.warning("No TE node for tool_call_id %s", tool_call_id)
            return HookResult(action="continue")

        child_session_id = data.get("child_session_id", "")
        child_agent = data.get("child_agent", "")

        # Add Delegation label and child properties
        properties: dict[str, Any] = {
            "child_session_id": child_session_id,
            "child_agent": child_agent,
        }
        await self.services.graph.upsert_node(te_id, {"Delegation"}, properties)

        # Create SPAWNED edge to child session
        if child_session_id:
            await self.services.graph.upsert_edge(
                te_id,
                child_session_id,
                "SPAWNED",
                {"occurred_at": data.get("timestamp", "")},
            )

        log.info("Delegation spawned from %s to %s", te_id, child_session_id)

        return HookResult(action="continue")

    async def _handle_delegate_agent_completed(
        self, data: dict[str, Any], log: EventLogContext
    ) -> HookResult:
        session_id = data.get("session_id")
        if not session_id:
            log.error("received event without session_id")
            return HookResult(action="continue")

        cursors = self.services.get_cursors(session_id)
        tool_call_id = data.get("tool_call_id", "")
        te_id = cursors.tool_call_map.get(tool_call_id)
        if not te_id:
            log.warning("No TE node for tool_call_id %s", tool_call_id)
            return HookResult(action="continue")

        timestamp = data.get("timestamp", "")
        properties: dict[str, Any] = {
            "delegate_completed_at": timestamp,
        }

        await self.services.graph.upsert_node(te_id, set(), properties)
        log.info("Delegation completed for %s", te_id)

        return HookResult(action="continue")
