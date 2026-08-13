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


def write_native(root: Path, project: str, sid: str, working_dir: str) -> str:
    ws = derive_workspace(working_dir)
    d = root / project / "sessions" / sid / "context-intelligence"
    d.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps({"event": name, "timestamp": ts, "workspace": ws, "data": data})
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
    for name, ts, data in _EVENTS:
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
    (d / "metadata.json").write_text(
        json.dumps({"working_dir": working_dir}), encoding="utf-8"
    )
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
