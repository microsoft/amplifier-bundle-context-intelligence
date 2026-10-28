"""Native Context Intelligence transcript retrieval tool."""

from __future__ import annotations

from typing import Any

__amplifier_module_type__ = "tool"
__all__ = ["mount"]


async def mount(coordinator: Any, config: Any) -> None:
    """Mount only the bounded native session transcript tool."""
    from .session_transcript_tool import SessionTranscriptTool

    tool = SessionTranscriptTool(coordinator, config)
    await coordinator.mount("tools", tool, name=tool.name)
