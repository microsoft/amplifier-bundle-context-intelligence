"""context_intelligence.reconstruct.transcript — transcript reconstruction from graph.

Extracts the conversation transcript for a session from the context-intelligence
graph server and reconstructs it as a list of message dicts in conversation order.

Level 1 (pure transforms):
    _extract_content_blocks(raw_response)
    _content_blocks_to_tool_calls(content_blocks)
    _make_assistant_content(content_blocks)
    _stringify_tool_result(result)

Level 3 (orchestration):
    extract_transcript(client, session_id, workspace) -> list[dict]

Extracted from prototype scripts/ci-reconstruct-sessions.py (lines 508-763).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from context_intelligence.client import CIClient, _safe_json_loads
from context_intelligence.reconstruct.events import extract_events

log = logging.getLogger("context_intelligence.reconstruct.transcript")


# ---------------------------------------------------------------------------
# Level 1 — Pure transforms
# ---------------------------------------------------------------------------


def _extract_content_blocks(raw_response: Any) -> list:
    """Extract content blocks from a resolved LLM response blob.

    The raw Anthropic response has a ``content`` list with blocks like:
    - {\"type\": \"text\", \"text\": \"...\"}
    - {\"type\": \"tool_use\", \"id\": \"toolu_...\", \"name\": \"bash\", \"input\": {...}}
    - {\"type\": \"thinking\", \"thinking\": \"...\", \"signature\": \"...\"}
    """
    if isinstance(raw_response, str):
        raw_response = _safe_json_loads(raw_response)
    if not isinstance(raw_response, dict):
        return []

    # The blob stores the full Anthropic API response object
    content = raw_response.get("content", [])
    if isinstance(content, list):
        return content

    # Some responses might wrap differently
    response = raw_response.get("response", {})
    if isinstance(response, dict):
        content = response.get("content", [])
        if isinstance(content, list):
            return content

    return []


def _content_blocks_to_tool_calls(content_blocks: list) -> list[dict]:
    """Extract tool_calls entries from content blocks.

    Returns list of ``{\"id\": ..., \"tool\": ..., \"arguments\": ...}`` dicts.
    Note: uses ``tool`` (not ``name``) and ``arguments`` (not ``input``)
    to match the hook-logging format.
    """
    result = []
    for block in content_blocks:
        if isinstance(block, dict) and block.get("type") in ("tool_use", "tool_call"):
            result.append(
                {
                    "id": block.get("id", ""),
                    "tool": block.get("name", ""),
                    "arguments": block.get("input", {}),
                }
            )
    return result


def _make_assistant_content(content_blocks: list) -> list:
    """Transform Anthropic content blocks into transcript-format content blocks.

    - Rename ``tool_use`` -> ``tool_call`` in content blocks.
    - Strip ``caller`` field from tool_use/tool_call blocks.
    - Add ``visibility: \"internal\"`` to thinking blocks (when not already set).
    """
    result = []
    for block in content_blocks:
        if not isinstance(block, dict):
            result.append(block)
            continue
        block = dict(block)  # shallow copy to avoid mutating originals

        # Rename tool_use -> tool_call
        if block.get("type") == "tool_use":
            block["type"] = "tool_call"

        # Strip caller field
        block.pop("caller", None)

        # Add visibility to thinking blocks
        if block.get("type") == "thinking" and "visibility" not in block:
            block["visibility"] = "internal"

        result.append(block)
    return result


def _stringify_tool_result(result: Any) -> str:
    """Convert a tool result to a string for the transcript."""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False)
    if isinstance(result, list):
        return json.dumps(result, ensure_ascii=False)
    return str(result)


# ---------------------------------------------------------------------------
# Level 3 — Orchestration (derives transcript from raw Event nodes)
# ---------------------------------------------------------------------------


def extract_transcript(
    client: CIClient,
    session_id: str,
    workspace: str,
) -> list[dict]:
    """Extract and reconstruct the transcript for a session.

    Derives the conversation transcript from raw ``Event`` nodes — the single
    source of truth in the live graph schema.  Blob refs in ``data.raw``
    (llm:response) and ``data.result`` (tool:post) are resolved before content
    extraction.

    Mapping:
    - ``prompt:submit``  → user message  (``data.prompt``)
    - ``llm:response``   → assistant message (``data.raw`` content blocks +
                           tool_calls derived from ``tool_use`` blocks)
    - ``tool:post``      → tool result message (``data.tool_call_id``,
                           ``data.tool_name``, ``data.result``)

    Parameters
    ----------
    client:
        Authenticated CIClient instance.
    session_id:
        The session whose transcript to extract.
    workspace:
        Workspace to scope all cypher queries.

    Returns
    -------
    list[dict]
        Messages in conversation order with role, content, and metadata fields.
    """
    messages: list[dict] = []

    # Fetch all events in chronological order with blob refs already resolved.
    # extract_events makes a single cypher call and handles blob resolution.
    events = extract_events(client, session_id, workspace, resolve_blobs=True)

    for ev in events:
        event_type = ev.get("event", "")
        data = ev.get("data") or {}
        ts = ev.get("ts", "")

        if event_type == "prompt:submit":
            # ── User message ─────────────────────────────────────────────────
            prompt_text = data.get("prompt", "")
            if prompt_text:
                messages.append(
                    {
                        "role": "user",
                        "content": prompt_text,
                        "metadata": {"timestamp": ts},
                    }
                )

        elif event_type == "llm:response":
            # ── Assistant message ─────────────────────────────────────────────
            # data["raw"] is already resolved by extract_events(resolve_blobs=True)
            raw_response = data.get("raw")
            content_blocks = _extract_content_blocks(raw_response)
            if content_blocks:
                assistant_content = _make_assistant_content(content_blocks)
                msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": assistant_content,
                    "metadata": {"timestamp": ts},
                }
                tool_calls = _content_blocks_to_tool_calls(content_blocks)
                if tool_calls:
                    msg["tool_calls"] = tool_calls
                messages.append(msg)

        elif event_type == "tool:post":
            # ── Tool result message ───────────────────────────────────────────
            # data["result"] is already resolved by extract_events(resolve_blobs=True)
            tool_name = data.get("tool_name", "")
            tool_call_id = data.get("tool_call_id", "")
            result = data.get("result")
            result_str = _stringify_tool_result(result) if result is not None else ""
            messages.append(
                {
                    "role": "tool",
                    "name": tool_name,
                    "tool_call_id": tool_call_id,
                    "content": result_str,
                    "metadata": {"timestamp": ts},
                }
            )

    return messages
