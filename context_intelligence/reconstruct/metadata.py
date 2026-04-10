"""context_intelligence.reconstruct.metadata — metadata extraction from graph.

Extracts session metadata from the context-intelligence graph server and
reconstructs it into the metadata.json format.

Level 2 — Network I/O (queries the CI graph server via CIClient).

Extracted from prototype scripts/ci-reconstruct-sessions.py (lines 771-1044).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from context_intelligence.client import CIClient, _safe_json_loads

log = logging.getLogger("context_intelligence.reconstruct.metadata")

# ---------------------------------------------------------------------------
# Level 1 helpers
# ---------------------------------------------------------------------------

# Matches subsession IDs: 0000000000000000-{child_span}_{agent_name}
# Group 1: child_span (hex characters after the dash)
# Group 2: agent_name (everything after the underscore separator)
_SUBSESSION_ID_RE = re.compile(r"^0{16}-([a-f0-9]+)_(.+)$")


def _extract_model_from_config(config: dict) -> str:
    """Extract the default model name from a session config blob.

    Looks for the provider with ``config.priority == 0`` and returns its
    ``config.default_model`` value.
    """
    providers = config.get("providers", [])
    if not isinstance(providers, list):
        return ""
    for provider in providers:
        if not isinstance(provider, dict):
            continue
        pcfg = provider.get("config", {})
        if isinstance(pcfg, dict) and pcfg.get("priority") == 0:
            return pcfg.get("default_model", "") or ""
    # Fallback: return first provider's model if any
    for provider in providers:
        if not isinstance(provider, dict):
            continue
        pcfg = provider.get("config", {})
        if isinstance(pcfg, dict):
            model = pcfg.get("default_model", "")
            if model:
                return model
    return ""


def _find_session_start_blob_key(blob_keys: set[str], session_id: str) -> str | None:
    """Find the session_start raw blob key from available blob keys.

    Key pattern: ``{session_id}__session_start__{epoch}__raw``
    """
    for key in blob_keys:
        if "session_start" in key and key.endswith("__raw"):
            return key
    return None


def _find_first_llm_request_blob_key(blob_keys: set[str]) -> str | None:
    """Find the first (earliest) llm_request raw blob key.

    Key pattern: ``{session_id}__llm_request__{epoch}__raw``
    Returns the key with the smallest epoch to get the first request.
    """
    candidates: list[str] = []
    for key in blob_keys:
        if "llm_request" in key and key.endswith("__raw"):
            candidates.append(key)
    if not candidates:
        return None
    # Sort by the epoch embedded in the key name to get the earliest
    candidates.sort()
    return candidates[0]


def build_disk_only_metadata(session_id: str, session_dir: Path) -> dict:
    """Build metadata for a session that exists on disk but not in the graph.

    Extracts what information it can from:
    - context-intelligence/metadata.json (CI hook metadata)
    - transcript.jsonl (line count for turn_count)
    - Directory ctime as fallback for created timestamp
    """
    metadata: dict[str, Any] = {
        "session_id": session_id,
        "incremental": True,
    }

    # Try to read CI metadata for timestamps and other info
    ci_meta_path = session_dir / "context-intelligence" / "metadata.json"
    if ci_meta_path.is_file():
        try:
            import json

            with open(ci_meta_path) as f:
                ci_meta = json.load(f)
            if isinstance(ci_meta, dict):
                if ci_meta.get("started_at"):
                    metadata["created"] = ci_meta["started_at"]
                if ci_meta.get("workspace"):
                    metadata["workspace"] = ci_meta["workspace"]
                if ci_meta.get("status"):
                    metadata["status"] = ci_meta["status"]
                if ci_meta.get("parent_id"):
                    metadata["parent_id"] = ci_meta["parent_id"]
                if ci_meta.get("working_dir"):
                    metadata["working_dir"] = ci_meta["working_dir"]
        except (Exception,) as exc:
            log.debug("  Could not read CI metadata: %s", exc)

    # Fallback: use directory ctime if no created timestamp
    if "created" not in metadata:
        try:
            ctime = session_dir.stat().st_ctime
            dt = datetime.fromtimestamp(ctime, tz=timezone.utc)
            metadata["created"] = dt.isoformat()
        except OSError:
            pass

    # Count turns from transcript.jsonl if it exists
    transcript_path = session_dir / "transcript.jsonl"
    if transcript_path.is_file():
        try:
            with open(transcript_path) as f:
                # Count user messages as turns
                turn_count = sum(1 for line in f if line.strip() and '"role":"user"' in line)
            metadata["turn_count"] = turn_count
        except OSError:
            metadata["turn_count"] = 0
    else:
        metadata["turn_count"] = 0

    return metadata


# ---------------------------------------------------------------------------
# Level 2 helpers
# ---------------------------------------------------------------------------


def _generate_session_name(client: CIClient, workspace: str, session_id: str) -> str:
    """Generate a session name from the first user prompt if available.

    Queries the first OrchestratorRun's prompt_preview and truncates it
    to a reasonable display length.
    """
    try:
        rows = client.cypher(
            f'MATCH (s:Session {{node_id: "{session_id}"}})- [:HAS_RUN]->(r:OrchestratorRun) '
            f"RETURN r.prompt_preview "
            f"ORDER BY r.started_at ASC LIMIT 1",
            workspace=workspace,
        )
        if not rows:
            return ""
        preview = rows[0].get("r.prompt_preview", "") or ""
        preview = preview.strip()
        if not preview:
            return ""
        if len(preview) > 50:
            return preview[:50] + "..."
        return preview
    except Exception as exc:
        log.debug("  Failed to generate session name: %s", exc)
        return ""


def _build_subsession_metadata(
    *,
    session_id: str,
    parent_id: str,
    started_at: str,
    turn_count: int,
    subsession_match: re.Match | None,  # type: ignore[type-arg]
) -> dict:
    """Build minimal metadata for a subsession."""
    child_span = ""
    agent_name = ""
    trace_id = parent_id

    if subsession_match:
        child_span = subsession_match.group(1)
        agent_name = subsession_match.group(2)
        # Normalize agent_name: underscores back to colons for display
        # e.g. "foundation_explorer" -> "foundation:explorer"
        if "_" in agent_name and ":" not in agent_name:
            agent_name = agent_name.replace("_", ":", 1)

    metadata: dict[str, Any] = {
        "session_id": session_id,
    }
    if parent_id:
        metadata["parent_id"] = parent_id
    if trace_id:
        metadata["trace_id"] = trace_id
    if agent_name:
        metadata["agent_name"] = agent_name
    if child_span:
        metadata["child_span"] = child_span
    if started_at:
        metadata["created"] = started_at
    metadata["turn_count"] = turn_count

    return metadata


def _build_root_metadata(
    *,
    client: CIClient,
    session_id: str,
    started_at: str,
    turn_count: int,
    session_data: dict,
) -> dict:
    """Build metadata for a root session, resolving the session_start blob."""
    metadata: dict[str, Any] = {
        "session_id": session_id,
    }

    if started_at:
        metadata["created"] = started_at

    # -- Try to resolve the session_start raw blob for rich metadata ---------
    bundle_name = ""
    model = ""
    working_dir = ""

    try:
        blob_keys = client.list_blob_keys(session_id)
        start_key = _find_session_start_blob_key(blob_keys, session_id)
        if start_key:
            blob_data = client.fetch_blob(session_id, start_key)
            if isinstance(blob_data, dict):
                bundle_name = blob_data.get("bundle_name", "") or ""
                working_dir = blob_data.get("working_dir", "") or ""
                model = _extract_model_from_config(blob_data)
                log.debug(
                    "  Resolved blob: bundle=%s, model=%s, working_dir=%s",
                    bundle_name,
                    model,
                    working_dir,
                )
            else:
                log.debug("  Session start blob was not a dict: %s", type(blob_data))
        else:
            # Fallback: try the first llm_request blob for at least the model
            log.debug(
                "  No session_start blob key found among %d keys, trying llm_request fallback",
                len(blob_keys),
            )
            llm_key = _find_first_llm_request_blob_key(blob_keys)
            if llm_key:
                llm_blob = client.fetch_blob(session_id, llm_key)
                if isinstance(llm_blob, dict):
                    model = llm_blob.get("model", "") or ""
                    log.debug("  Resolved model from llm_request blob: %s", model)
                else:
                    log.debug("  llm_request blob was not a dict: %s", type(llm_blob))
            else:
                log.debug("  No llm_request blob keys found either")
    except Exception as exc:
        log.debug("  Failed to resolve session_start blob: %s", exc)

    # -- Populate fields (use "bundle:" prefix for bundle_name) --------------
    if bundle_name:
        # Ensure "bundle:" prefix for consistency with amplifier resume format
        if not bundle_name.startswith("bundle:"):
            bundle_name = f"bundle:{bundle_name}"
        metadata["bundle"] = bundle_name

    if model:
        metadata["model"] = model

    metadata["turn_count"] = turn_count
    metadata["incremental"] = True

    if working_dir:
        metadata["working_dir"] = working_dir

    # name/description are not available in the graph (set by hooks-session-naming)
    # They remain absent; amplifier resume will show them as missing but the session
    # will at least have bundle, model, and turn_count populated.

    return metadata


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_metadata(
    client: CIClient,
    workspace: str,
    session_id: str,
) -> dict | None:
    """Extract metadata for a session from the graph.

    Returns a metadata dict suitable for writing as ``metadata.json``, or
    *None* if the session cannot be found.
    """
    # -- Query session node for basic properties -----------------------------
    rows = client.cypher(
        f'MATCH (s:Session {{node_id: "{session_id}"}}) '
        f"RETURN s.node_id, s.started_at, s.ended_at, s.status, s.data",
        workspace=workspace,
    )
    if not rows:
        log.warning("  No Session node found for %s", session_id)
        return None

    row = rows[0]
    started_at = row.get("s.started_at", "") or ""
    session_data_str = row.get("s.data")

    # -- Parse Session.data to extract parent_id -----------------------------
    session_data = _safe_json_loads(session_data_str) if session_data_str else {}
    if not isinstance(session_data, dict):
        session_data = {}
    parent_id = session_data.get("parent_id") or ""

    # -- Count OrchestratorRun nodes for turn_count --------------------------
    run_rows = client.cypher(
        f'MATCH (s:Session {{node_id: "{session_id}"}})- [:HAS_RUN]->(r:OrchestratorRun) '
        f"RETURN count(r) AS turn_count",
        workspace=workspace,
    )
    turn_count = 0
    if run_rows:
        turn_count = run_rows[0].get("turn_count", 0)

    # -- Detect root vs subsession -------------------------------------------
    subsession_match = _SUBSESSION_ID_RE.match(session_id)
    is_subsession = bool(subsession_match) or bool(parent_id)

    if is_subsession:
        return _build_subsession_metadata(
            session_id=session_id,
            parent_id=parent_id,
            started_at=started_at,
            turn_count=turn_count,
            subsession_match=subsession_match,
        )

    metadata = _build_root_metadata(
        client=client,
        session_id=session_id,
        started_at=started_at,
        turn_count=turn_count,
        session_data=session_data,
    )

    # -- Generate session name from first user prompt if missing --------------
    if not metadata.get("name"):
        name = _generate_session_name(client, workspace, session_id)
        if name:
            metadata["name"] = name

    return metadata
