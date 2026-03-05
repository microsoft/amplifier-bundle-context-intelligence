"""Tests for all 7 handlers — protocol conformance, event claims, disjointness."""

from __future__ import annotations


import pytest
from amplifier_core.models import HookResult

from amplifier_module_hook_context_intelligence.handlers.default import DefaultHandler
from amplifier_module_hook_context_intelligence.handlers.event import SystemEventHandler
from amplifier_module_hook_context_intelligence.handlers.orchestrator_run import (
    OrchestratorRunHandler,
)
from amplifier_module_hook_context_intelligence.handlers.recipe_step import RecipeStepHandler
from amplifier_module_hook_context_intelligence.handlers.session import SessionHandler
from amplifier_module_hook_context_intelligence.handlers.step import StepHandler
from amplifier_module_hook_context_intelligence.handlers.tool_execution import ToolExecutionHandler
from amplifier_module_hook_context_intelligence.protocol import EventHandler
from amplifier_module_hook_context_intelligence.services import HookStateService

ENTITY_HANDLER_CLASSES = [
    SessionHandler,
    OrchestratorRunHandler,
    StepHandler,
    RecipeStepHandler,
    ToolExecutionHandler,
    SystemEventHandler,
]

ALL_HANDLER_CLASSES = ENTITY_HANDLER_CLASSES + [DefaultHandler]


class TestProtocolConformance:
    @pytest.mark.parametrize("handler_cls", ALL_HANDLER_CLASSES)
    def test_handler_conforms_to_protocol(self, handler_cls, services: HookStateService):
        handler = handler_cls(services)
        assert isinstance(handler, EventHandler)

    @pytest.mark.parametrize("handler_cls", ALL_HANDLER_CLASSES)
    def test_handler_has_handled_events_set(self, handler_cls, services: HookStateService):
        handler = handler_cls(services)
        assert isinstance(handler.handled_events, (set, frozenset))

    @pytest.mark.parametrize("handler_cls", ALL_HANDLER_CLASSES)
    def test_handler_has_services(self, handler_cls, services: HookStateService):
        handler = handler_cls(services)
        assert handler.services is services

    @pytest.mark.parametrize("handler_cls", ALL_HANDLER_CLASSES)
    async def test_handler_returns_hook_result(self, handler_cls, services: HookStateService):
        handler = handler_cls(services)
        events = handler.handled_events
        event = next(iter(events)) if events else "test:synthetic"
        result = await handler(event, {"timestamp": "2026-01-01T00:00:00Z"})
        assert isinstance(result, HookResult)
        assert result.action == "continue"


class TestEventClaims:
    def test_session_handler_events(self, services: HookStateService):
        handler = SessionHandler(services)
        assert handler.handled_events == {
            "session:start",
            "session:fork",
            "session:end",
            "session:resume",
        }

    def test_orchestrator_run_handler_events(self, services: HookStateService):
        handler = OrchestratorRunHandler(services)
        assert handler.handled_events == {
            "prompt:submit",
            "execution:start",
            "execution:end",
            "orchestrator:complete",
        }

    def test_step_handler_events(self, services: HookStateService):
        handler = StepHandler(services)
        expected = {
            "provider:request",
            "llm:response",
            "llm:request:*",
            "llm:response:*",
            "content_block:*",
        }
        assert handler.handled_events == expected

    def test_recipe_step_handler_events(self, services: HookStateService):
        handler = RecipeStepHandler(services)
        assert handler.handled_events == {
            "recipe:step_started",
            "recipe:step_completed",
            "recipe:approval:*",
        }

    def test_tool_execution_handler_events(self, services: HookStateService):
        handler = ToolExecutionHandler(services)
        assert handler.handled_events == {
            "tool:pre",
            "tool:post",
            "tool:error",
            "delegate:agent_spawned",
            "delegate:agent_completed",
            "delegate:context_inherited",
            "delegate:session_resumed",
        }

    def test_system_event_handler_events(self, services: HookStateService):
        handler = SystemEventHandler(services)
        assert handler.handled_events == {
            "context:compaction",
            "cancel:requested",
            "cancel:completed",
        }

    def test_default_handler_starts_empty(self, services: HookStateService):
        handler = DefaultHandler(services)
        assert handler.handled_events == set()


class TestDisjointness:
    def test_entity_handler_events_are_disjoint(self, services: HookStateService):
        all_events: list[str] = []
        for handler_cls in ENTITY_HANDLER_CLASSES:
            handler = handler_cls(services)
            all_events.extend(handler.handled_events)
        assert len(all_events) == len(set(all_events)), (
            f"Duplicate events found: {[e for e in all_events if all_events.count(e) > 1]}"
        )

    def test_claimed_events_union(self, services: HookStateService):
        claimed: set[str] = set()
        for handler_cls in ENTITY_HANDLER_CLASSES:
            handler = handler_cls(services)
            claimed |= handler.handled_events
        assert len(claimed) >= 18, f"Expected at least 18 claimed events, got {len(claimed)}"


class TestDefaultHandlerLabelDerivation:
    def test_derive_label_simple(self):
        assert DefaultHandler.derive_label("context:compaction") == "ContextCompaction"

    def test_derive_label_multi_part(self):
        assert DefaultHandler.derive_label("delegate:agent_spawned") == "DelegateAgentSpawned"

    def test_derive_label_three_segments(self):
        assert DefaultHandler.derive_label("llm:request:raw") == "LlmRequestRaw"
