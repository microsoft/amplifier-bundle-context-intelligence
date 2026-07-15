#!/usr/bin/env python3
"""Generate the native-CI twin for each legacy session under a fixture root,
using the upload tool's OWN transform. Single source of truth: no hand-authored
event data, no machine-specific values -- every field is derived from the
committed legacy fixture. Run inside the DTU with the tool's python (TOOL_PY).

Usage: gen_native_twin.py <fixture_legacy_root>
  <fixture_legacy_root>/sessions/<sid>/{events.jsonl,metadata.json}  (input)
  <fixture_legacy_root>/sessions/<sid>/context-intelligence/...      (output)
"""

import json
import sys
from pathlib import Path

from amplifier_module_tool_context_intelligence_upload.legacy_transform import (
    derive_workspace,
    reassemble_event_data,
)


def gen_session(session_dir: Path) -> str:
    meta = json.loads((session_dir / "metadata.json").read_text(encoding="utf-8"))
    working_dir = meta["working_dir"]
    workspace = derive_workspace(working_dir)
    sid = session_dir.name

    out_dir = session_dir / "context-intelligence"
    out_dir.mkdir(parents=True, exist_ok=True)

    started_at = ""
    lines = []
    for raw in (session_dir / "events.jsonl").read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        rec = json.loads(raw)
        event_name, data = reassemble_event_data(rec)
        ts = data.get("timestamp", "")
        if not started_at:
            started_at = ts
        lines.append(
            json.dumps({"event": event_name, "timestamp": ts, "workspace": workspace, "data": data})
        )
    (out_dir / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out_dir / "metadata.json").write_text(
        json.dumps(
            {
                "format": "context-intelligence",
                "session_id": sid,
                "parent_id": "",
                "status": "completed",
                "started_at": started_at,
                "workspace": workspace,
            }
        ),
        encoding="utf-8",
    )
    return workspace


def main() -> int:
    root = Path(sys.argv[1])
    sessions = sorted((root / "sessions").glob("*/metadata.json"))
    if not sessions:
        print(f"FATAL: no legacy sessions under {root}/sessions", file=sys.stderr)
        return 1
    for meta_path in sessions:
        ws = gen_session(meta_path.parent)
        print(f"generated twin: {meta_path.parent.name} -> workspace={ws}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
