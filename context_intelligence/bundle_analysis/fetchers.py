"""context_intelligence.bundle_analysis.fetchers — raw signal event normalization.

Normalizes inputs from JSONL records into a common RawSignalEvent dataclass with
four discriminated kinds:

  agent_spawned    — delegate:agent_spawned events; agent field carries bundle:component
  skill_loaded     — skill:loaded events; skill_source carries the skill file path
  recipe_execute   — tool:pre events for the 'recipes' tool with operation='execute'
  mentions_resolved — mentions:resolved events; resolutions carries the list of resolved mentions

JSONLFetcher reads local events.jsonl files from the Amplifier projects directory
and normalizes each record.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger("context_intelligence.bundle_analysis.fetchers")

# ---------------------------------------------------------------------------
# Discriminated union kind
# ---------------------------------------------------------------------------

SignalKind = Literal[
    "agent_spawned",
    "skill_loaded",
    "recipe_execute",
    "mentions_resolved",
    "tool_call",
    "mode_activated",
]


# ---------------------------------------------------------------------------
# RawSignalEvent
# ---------------------------------------------------------------------------


@dataclass
class RawSignalEvent:
    """A normalised attribution signal from one of the four tracked event types.

    Only the field corresponding to *kind* is populated; the others are ``None``.

    Attributes
    ----------
    kind:
        Discriminator — one of ``agent_spawned``, ``skill_loaded``,
        ``recipe_execute``, ``mentions_resolved``.
    agent:
        Populated for ``agent_spawned`` — ``bundle:component`` string.
    skill_source:
        Populated for ``skill_loaded`` — absolute path to the skill file.
    recipe_path:
        Populated for ``recipe_execute`` — recipe path argument.
    resolutions:
        Populated for ``mentions_resolved`` — list of resolved-mention dicts.
    """

    kind: SignalKind
    agent: str | None = None
    skill_source: str | None = None
    recipe_path: str | None = None
    resolutions: list[dict[str, Any]] | None = None
    tool_name: str | None = None
    mode_name: str | None = None

    # ------------------------------------------------------------------
    # Graph-row constructor
    # ------------------------------------------------------------------

    @classmethod
    def from_graph_row(cls, row: Any) -> "RawSignalEvent | None":
        """Construct a RawSignalEvent from a Cypher result row dict.

        Expected row columns:
            event_name, agent, tool_name, tool_input_json, data_json

        Returns ``None`` for unrecognised or malformed rows.
        """
        if not isinstance(row, dict):
            return None

        event_name: str | None = row.get("event_name")
        tool_name: str | None = row.get("tool_name")

        # ---- agent_spawned ----
        if event_name == "delegate:agent_spawned":
            agent = row.get("agent")
            if not isinstance(agent, str):
                return None
            return cls(kind="agent_spawned", agent=agent)

        # ---- skill_loaded ----
        if event_name == "skill:loaded":
            data = _coerce_json(row.get("data_json"))
            if not isinstance(data, dict):
                return None
            source = data.get("source")
            if not isinstance(source, str):
                return None
            return cls(kind="skill_loaded", skill_source=source)

        # ---- recipe_execute (tool:pre for 'recipes') ----
        if tool_name == "recipes":
            tool_input = _coerce_json(row.get("tool_input_json"))
            if not isinstance(tool_input, dict):
                return None
            if tool_input.get("operation") != "execute":
                return None
            recipe_path = tool_input.get("recipe_path")
            if not isinstance(recipe_path, str):
                return None
            return cls(kind="recipe_execute", recipe_path=recipe_path)

        # ---- mentions_resolved ----
        if event_name == "mentions:resolved":
            data = _coerce_json(row.get("data_json"))
            if not isinstance(data, dict):
                data = {}
            resolutions = data.get("resolutions")
            if not isinstance(resolutions, list):
                resolutions = []
            return cls(kind="mentions_resolved", resolutions=resolutions)

        return None

    # ------------------------------------------------------------------
    # JSONL-record constructor
    # ------------------------------------------------------------------

    @classmethod
    def from_jsonl_event(cls, record: Any) -> "RawSignalEvent | None":
        """Construct a RawSignalEvent from a JSONL events.jsonl record dict.

        Expected record keys:
            event  — event name string
            data   — event payload dict

        Returns ``None`` for unrecognised or malformed records.
        """
        if not isinstance(record, dict):
            return None

        event_name: str | None = record.get("event")
        data: Any = record.get("data") or {}

        # ---- agent_spawned ----
        if event_name == "delegate:agent_spawned":
            agent = data.get("agent") if isinstance(data, dict) else None
            if not isinstance(agent, str):
                return None
            return cls(kind="agent_spawned", agent=agent)

        # ---- skill_loaded ----
        if event_name == "skill:loaded":
            if not isinstance(data, dict):
                return None
            source = data.get("source")
            if not isinstance(source, str):
                return None
            return cls(kind="skill_loaded", skill_source=source)

        # ---- recipe_execute OR tool_call (both come from tool:pre events) ----
        if event_name == "tool:pre":
            if not isinstance(data, dict):
                return None
            tool_name = data.get("tool_name")
            if not isinstance(tool_name, str) or not tool_name:
                return None
            if tool_name == "recipes":
                tool_input = _coerce_json(data.get("tool_input"))
                if not isinstance(tool_input, dict):
                    return None
                if tool_input.get("operation") != "execute":
                    return None
                recipe_path = tool_input.get("recipe_path")
                if not isinstance(recipe_path, str):
                    return None
                return cls(kind="recipe_execute", recipe_path=recipe_path)
            # Any non-recipe tool
            return cls(kind="tool_call", tool_name=tool_name)

        # ---- mode_activated (mode:activated and mode:changed both contribute) ----
        if event_name == "mode:activated":
            if not isinstance(data, dict):
                return None
            mode_name = data.get("mode")
            if not isinstance(mode_name, str) or not mode_name:
                return None
            return cls(kind="mode_activated", mode_name=mode_name)

        if event_name == "mode:changed":
            if not isinstance(data, dict):
                return None
            # to_mode and new are aliases; prefer to_mode
            mode_name = data.get("to_mode") or data.get("new")
            if not isinstance(mode_name, str) or not mode_name:
                return None
            return cls(kind="mode_activated", mode_name=mode_name)

        # ---- mentions_resolved ----
        if event_name == "mentions:resolved":
            resolutions = data.get("resolutions") if isinstance(data, dict) else None
            if not isinstance(resolutions, list):
                resolutions = []
            return cls(kind="mentions_resolved", resolutions=resolutions)

        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _coerce_json(value: Any) -> Any:
    """Return *value* parsed as JSON when it is a str; return it as-is otherwise.

    Returns ``None`` for unparseable strings or ``None`` input.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# JSONLFetcher
# ---------------------------------------------------------------------------


class JSONLFetcher:
    """Fetches raw attribution signals from local events.jsonl files.

    Reads from ``{base_path}/{workspace}/sessions/{session_id}/context-intelligence/events.jsonl``
    for session scope, or globs all sessions under the workspace for workspace scope.
    """

    def fetch(
        self,
        *,
        workspace: str,
        session_id: str | None,
        base_path: Path | None,
    ) -> list[RawSignalEvent]:
        """Read events.jsonl file(s) and return normalised events.

        Parameters
        ----------
        workspace:
            Workspace slug.
        session_id:
            Optional session ID; when provided only that session's file is read.
        base_path:
            Root of the Amplifier projects directory. Defaults to
            ``~/.amplifier/projects`` when ``None``.

        Returns
        -------
        list[RawSignalEvent]
            Normalised events from the file(s). Returns ``[]`` when files are
            missing or the workspace directory does not exist.
        """
        base = base_path if base_path is not None else Path.home() / ".amplifier" / "projects"
        sessions_dir = base / workspace / "sessions"

        if not sessions_dir.exists():
            return []

        if session_id is not None:
            paths = [sessions_dir / session_id / "context-intelligence" / "events.jsonl"]
        else:
            paths = sorted(sessions_dir.glob("*/context-intelligence/events.jsonl"))

        events: list[RawSignalEvent] = []
        for path in paths:
            if not path.exists():
                continue
            try:
                with path.open(encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(record, dict):
                            continue
                        evt = RawSignalEvent.from_jsonl_event(record)
                        if evt is not None:
                            events.append(evt)
            except OSError as exc:
                logger.debug("JSONLFetcher: could not read %s: %s", path, exc)
                continue

        return events


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = ["RawSignalEvent", "JSONLFetcher"]
