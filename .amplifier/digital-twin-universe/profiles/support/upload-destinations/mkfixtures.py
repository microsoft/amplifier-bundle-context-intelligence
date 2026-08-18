#!/usr/bin/env python3
"""Build the upload-destinations e2e fixture tree under ~/.amplifier/projects.

Run INSIDE the DTU with the tool's own interpreter (TOOL_PY), so the
workspace slugs come from the code under test rather than a literal.

Layouts produced (both discovered by the tool's own discover_fn):

  CI-native  ~/.amplifier/projects/<proj>/sessions/<sid>/context-intelligence/
                 {metadata.json, events.jsonl}
  legacy     ~/.amplifier/projects/<proj>/sessions/<sid>/
                 {metadata.json, events.jsonl}

Working dirs: one KEEP (matched by the destination include), one SKIP
(matched by the destination exclude). Prints a KEY=VALUE block that the
scenario scripts source, so every expected workspace name downstream is
runtime-derived.

Usage: mkfixtures.py <projects_root>
"""

import json
import sys
from pathlib import Path

from amplifier_module_tool_context_intelligence_upload.legacy_transform import derive_workspace

KEEP_DIR = "/home/amplifier/workspaces/keep-me"
SKIP_DIR = "/home/amplifier/workspaces/skip-me"

_EVENTS = [
    ("session:start", "2026-02-01T00:00:00.000+00:00", {"note": "e2e fixture"}),
    ("tool:call", "2026-02-01T00:00:01.000+00:00", {"tool_name": "bash"}),
    ("tool:result", "2026-02-01T00:00:02.000+00:00", {"tool_name": "bash"}),
]

# Legacy (hooks-logging) discovery classifies any session with NO terminal
# event as live/in-progress and SKIPS it -- see logging_hook_format.py:
# _has_terminal_event / _TERMINAL_EVENTS = {"session:end",
# "orchestrator:complete", "execution:end"}. A finished, hook-logged session on
# disk always closes with one of those; a fixture that omits it is not a
# completed session, so every legacy fixture would be skipped and S6 would
# discover 0 sessions (exercising nothing). Append a real terminal event so the
# legacy fixtures are genuinely complete and discoverable.
_LEGACY_TERMINAL = (
    "session:end",
    "2026-02-01T00:00:03.000+00:00",
    {"status": "completed"},
)


def write_native(root: Path, project: str, sid: str, working_dir: str) -> str:
    ws = derive_workspace(working_dir)
    d = root / project / "sessions" / sid / "context-intelligence"
    d.mkdir(parents=True, exist_ok=True)
    # Real CI-native events carry the timestamp BOTH at the top level and
    # inside `data` (verified against genuine ~/.amplifier/projects/**/
    # context-intelligence/events.jsonl, whose data keys are
    # metadata,parent,parent_id,raw,session_id,timestamp). The ingest server
    # hard-requires `data.timestamp` -- see context_intelligence_server/
    # main.py:_validate_data_timestamp, whose own docstring records
    # "Real Amplifier clients always supply data.timestamp (verified:
    # 224,530 events on disk, 0 missing)". A fixture that omits it is not a
    # native event, so reproduce the real shape here.
    lines = [
        json.dumps(
            {
                "event": name,
                "timestamp": ts,
                "workspace": ws,
                "data": {**data, "timestamp": ts, "session_id": sid},
            }
        )
        for name, ts, data in _EVENTS
    ]
    (d / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (d / "metadata.json").write_text(
        json.dumps(
            {
                "format": "context-intelligence",
                "session_id": sid,
                "parent_id": "",
                "status": "completed",
                "started_at": _EVENTS[0][1],
                "workspace": ws,
                "working_dir": working_dir,
            }
        ),
        encoding="utf-8",
    )
    return ws


def write_legacy(root: Path, project: str, sid: str, working_dir: str) -> str:
    ws = derive_workspace(working_dir)
    d = root / project / "sessions" / sid
    d.mkdir(parents=True, exist_ok=True)
    lines = []
    # The session:start body plus a terminal session:end make this a COMPLETE
    # legacy session; without the terminal event discovery skips it as
    # live/in-progress (see _LEGACY_TERMINAL above).
    for name, ts, data in (*_EVENTS, _LEGACY_TERMINAL):
        payload = dict(data)
        if name == "session:start":
            payload["working_dir"] = working_dir
        lines.append(
            json.dumps(
                {
                    "event": name,
                    "schema": {"name": "amplifier.log", "ver": "1.0.0"},
                    "ts": ts,
                    "session_id": sid,
                    "status": "ok",
                    "data": payload,
                }
            )
        )
    (d / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (d / "metadata.json").write_text(json.dumps({"working_dir": working_dir}), encoding="utf-8")
    return ws


def main() -> int:
    root = Path(sys.argv[1])
    ws_keep = write_native(root, "native-keep", "nat-keep-001", KEEP_DIR)
    ws_skip = write_native(root, "native-skip", "nat-skip-001", SKIP_DIR)
    ws_keep_legacy = write_legacy(root, "legacy-keep", "leg-keep-001", KEEP_DIR)
    ws_skip_legacy = write_legacy(root, "legacy-skip", "leg-skip-001", SKIP_DIR)

    assert ws_keep == ws_keep_legacy, (ws_keep, ws_keep_legacy)
    assert ws_skip == ws_skip_legacy, (ws_skip, ws_skip_legacy)

    print(f"WS_KEEP={ws_keep}")
    print(f"WS_SKIP={ws_skip}")
    print(f"KEEP_DIR={KEEP_DIR}")
    print(f"SKIP_DIR={SKIP_DIR}")
    print("NATIVE_SESSIONS=2")
    print("LEGACY_SESSIONS=2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
