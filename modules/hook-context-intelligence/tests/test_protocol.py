"""Tests for the EventHandler protocol."""

from __future__ import annotations

from typing import Any

from amplifier_core.models import HookResult


def test_event_handler_is_runtime_checkable():
    from amplifier_module_hook_context_intelligence.protocol import EventHandler

    assert hasattr(EventHandler, "__protocol_attrs__") or hasattr(
        EventHandler, "_is_runtime_protocol"
    )


def test_conforming_class_passes_isinstance():
    from amplifier_module_hook_context_intelligence.protocol import EventHandler

    class FakeHandler:
        handled_events: set[str] = {"test:event"}
        services = None

        async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
            return HookResult(action="continue")

    handler = FakeHandler()
    assert isinstance(handler, EventHandler)


def test_missing_handled_events_fails_isinstance():
    from amplifier_module_hook_context_intelligence.protocol import EventHandler

    class BadHandler:
        services = None

        async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
            return HookResult(action="continue")

    handler = BadHandler()
    assert not isinstance(handler, EventHandler)


def test_missing_call_fails_isinstance():
    from amplifier_module_hook_context_intelligence.protocol import EventHandler

    class BadHandler:
        handled_events: set[str] = {"test:event"}
        services = None

    handler = BadHandler()
    assert not isinstance(handler, EventHandler)
