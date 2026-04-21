"""context_intelligence.reconstruct.transcript — transcript reconstruction from graph.

Extracts the conversation transcript for a session from the context-intelligence
graph server and reconstructs it as a list of message dicts in conversation order.

Level 1 (pure transforms):
    _extract_content_blocks(raw_response)
    _content_blocks_to_tool_calls(content_blocks)
    _make_assistant_content(content_blocks)
    _stringify_tool_result(result)

Level 2 (network I/O):
    _resolve_blob_ref(data, field, blob_index, client, session_id)

Level 3 (orchestration):
    extract_transcript(client, session_id, workspace) -> list[dict]

Extracted from prototype scripts/ci-reconstruct-sessions.py (lines 508-763).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from context_intelligence.client import CIClient, _safe_json_loads

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
# Level 2 — Network I/O (blob resolution with cache)
# ---------------------------------------------------------------------------


def _resolve_blob_ref(
    data: dict,
    field: str,
    blob_index: dict[str, Any],
    client: CIClient,
    session_id: str,
) -> Any:
    """Resolve a ``$blob_ref`` within a parsed data dict for the given field.

    Returns the resolved data, or the original value if no blob ref.
    The *blob_index* is a mutable cache dict shared across calls to avoid
    redundant fetches within a session.
    """
    value = data.get(field)
    if isinstance(value, dict) and "$blob_ref" in value:
        uri = value["$blob_ref"]
        # uri format: ci-blob://SESSION_ID/KEY
        if uri.startswith("ci-blob://"):
            parts = uri[len("ci-blob://") :].split("/", 1)
            if len(parts) == 2:
                blob_session, blob_key = parts
                # Try the pre-fetched index first
                if blob_key in blob_index:
                    return blob_index[blob_key]
                # Fall back to individual fetch
                fetched = client.fetch_blob(blob_session, blob_key)
                if fetched is not None:
                    blob_index[blob_key] = fetched  # cache it
                    return fetched
        log.debug("Could not resolve blob ref: %s", uri)
        return value
    return value


# ---------------------------------------------------------------------------
# Level 3 — Orchestration (walks the graph structure)
# ---------------------------------------------------------------------------


def extract_transcript(
    client: CIClient,
    session_id: str,
    workspace: str,
) -> list[dict]:
    """Extract and reconstruct the transcript for a session.

    Walks the graph structure:
    Session -> OrchestratorRun (ordered by started_at)
            -> PromptStep (user messages)
            -> AssistantStep chain (via NEXT, ordered by iteration, deduplicated)
            -> ToolExecution (triggered by each step)

    Returns a list of message dicts (user, assistant, tool) in conversation order.

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

    # List available blobs; actual data fetched on demand via fetch_blob()
    log.debug("Listing available blobs for session %s ...", session_id)
    available_keys = client.list_blob_keys(session_id)
    blob_index: dict[str, Any] = {}  # cache for fetched blobs
    log.debug("Found %d blob keys for session %s", len(available_keys), session_id)

    # ── Get all runs ordered by started_at ────────────────────────────────
    runs = client.cypher(
        f'MATCH (s:Session {{node_id: "{session_id}"}}) -[:HAS_RUN]->(r:OrchestratorRun) '
        f"RETURN r.node_id, r.started_at "
        f"ORDER BY r.started_at",
        workspace=workspace,
    )

    for run in runs:
        run_id = run.get("r.node_id", "")
        if not run_id:
            continue

        # ── PromptStep → user message ──────────────────────────────────────
        prompts = client.cypher(
            f'MATCH (r:OrchestratorRun {{node_id: "{run_id}"}}) -[:HAS_STEP]->(p:PromptStep) '
            f"RETURN p.prompt_text, p.occurred_at "
            f"ORDER BY p.occurred_at",
            workspace=workspace,
        )
        for prompt in prompts:
            prompt_text = prompt.get("p.prompt_text", "")
            if prompt_text:
                messages.append(
                    {
                        "role": "user",
                        "content": prompt_text,
                        "metadata": {"timestamp": prompt.get("p.occurred_at", "")},
                    }
                )

        # ── Walk the AssistantStep chain (ordered by iteration) ────────────
        # Get all AssistantSteps for this run via the prompt->NEXT->assistant chain
        steps = client.cypher(
            f'MATCH (r:OrchestratorRun {{node_id: "{run_id}"}}) -[:HAS_STEP]->(p:PromptStep) '
            f"MATCH path = (p)-[:NEXT*]->(a:AssistantStep) "
            f"RETURN DISTINCT a.node_id, a.iteration, a.response_at, "
            f"a.data_llm_response "
            f"ORDER BY a.iteration",
            workspace=workspace,
        )

        # Deduplicate in case the graph traversal returns duplicates
        seen_steps: set[str] = set()

        for step in steps:
            step_id = step.get("a.node_id", "")
            if not step_id or step_id in seen_steps:
                continue
            seen_steps.add(step_id)

            # ── Resolve LLM response blob → assistant message ────────────
            llm_resp_str = step.get("a.data_llm_response")
            llm_resp_data = _safe_json_loads(llm_resp_str) if llm_resp_str else None

            content_blocks: list = []
            if isinstance(llm_resp_data, dict):
                raw_response = _resolve_blob_ref(
                    llm_resp_data, "raw", blob_index, client, session_id
                )
                content_blocks = _extract_content_blocks(raw_response)

            if content_blocks:
                assistant_content = _make_assistant_content(content_blocks)
            elif isinstance(llm_resp_data, dict):
                # No blob resolution possible — use what we have
                # If there's a stop_reason of end_turn with no content, skip
                assistant_content = []
            else:
                assistant_content = []

            if assistant_content:
                msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": assistant_content,
                    "metadata": {"timestamp": step.get("a.response_at", "")},
                }
                # Generate tool_calls top-level array
                tool_calls = _content_blocks_to_tool_calls(content_blocks)
                if tool_calls:
                    msg["tool_calls"] = tool_calls
                messages.append(msg)

            # ── Tool executions triggered by this assistant step ──────────
            tools = client.cypher(
                f'MATCH (a:AssistantStep {{node_id: "{step_id}"}}) -[:TRIGGERED]->(t:ToolExecution) '
                f"RETURN t.tool_name, t.tool_call_id, t.ended_at, "
                f"t.data_tool_post "
                f"ORDER BY t.started_at",
                workspace=workspace,
            )

            for tool in tools:
                tool_name = tool.get("t.tool_name", "")
                tool_call_id = tool.get("t.tool_call_id", "")
                tool_post_str = tool.get("t.data_tool_post")

                result_str = ""
                if tool_post_str:
                    tool_post = _safe_json_loads(tool_post_str)
                    if isinstance(tool_post, dict):
                        result = _resolve_blob_ref(
                            tool_post, "result", blob_index, client, session_id
                        )
                        result_str = _stringify_tool_result(result)
                    else:
                        result_str = _stringify_tool_result(tool_post)

                messages.append(
                    {
                        "role": "tool",
                        "name": tool_name,
                        "tool_call_id": tool_call_id,
                        "content": result_str,
                        "metadata": {"timestamp": tool.get("t.ended_at", "")},
                    }
                )

    return messages
