#!/usr/bin/env python3
"""Render this repo's catalog surfaces from a scratch Amplifier session.

Two surfaces are rendered, both from a real mounted session and with **no LLM
call** ($0 spend):

  1. the ``delegate`` tool's "Available agents:" catalog block, and
  2. the ``<system-reminder source="hooks-skills-visibility">`` block.

Both are injected into every session's head on every turn, so their byte size
is a per-turn tax. This script is the measurement instrument for lane kv98:
run it at HEAD, edit the descriptions, run it again, diff the byte counts.

The scratch bundle is written INTO the repo checkout and points at LOCAL
files, never at the published ``@main`` sources -- so it measures the working
tree, which is the entire point of a before/after. Two details make that work
and are easy to get wrong:

* the bundle YAML must sit at the repo root, because ``resolve_agent_path()``
  resolves an agent to ``<bundle base_path>/agents/<name>.md``; and
* a LOCAL skill source must be a bare filesystem path -- ``resolve_skill_source``
  treats anything that is not ``git+``/``https://`` as a path and silently
  returns None for a ``file://`` URI, which falls back to the machine's default
  skills directories and measures the wrong repo entirely.

Usage:
    python docs/lanes/<lane>/render_catalog.py --repo . --out <dir>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

FOUNDATION = "git+https://github.com/microsoft/amplifier-foundation@main"
SKILLS_BUNDLE = "git+https://github.com/microsoft/amplifier-bundle-skills@main"
PROBE_BUNDLE_NAME = "kv98probe"

SKILLS_PATTERN = re.compile(
    r'<system-reminder source="hooks-skills-visibility">.*?</system-reminder>',
    re.S,
)


def build_scratch_bundle(repo: Path) -> Path:
    """Write a scratch bundle YAML at the repo root wiring local agents+skills."""
    agents = sorted(p.stem for p in (repo / "agents").glob("*.md"))
    skills_root = (repo / "skills").resolve()

    lines = [
        "bundle:",
        f"  name: {PROBE_BUNDLE_NAME}",
        "  version: 0.0.0",
        "  description: Scratch bundle for lane kv98 catalog measurement.",
        "",
        "session:",
        "  orchestrator:",
        "    module: loop-basic",
        "    source: git+https://github.com/microsoft/amplifier-module-loop-basic@main",
        "  context:",
        "    module: context-simple",
        "    source: git+https://github.com/microsoft/amplifier-module-context-simple@main",
        "",
        "agents:",
    ]
    for name in agents:
        lines.append(f"  {name}: {{}}")
    lines += [
        "",
        "tools:",
        "  - module: tool-delegate",
        f"    source: {FOUNDATION}#subdirectory=modules/tool-delegate",
        "  - module: tool-skills",
        f"    source: {SKILLS_BUNDLE}#subdirectory=modules/tool-skills",
        "    config:",
        "      visibility:",
        "        placement: request",
        "      skills:",
        f'        - "{skills_root}"',
        "",
    ]

    path = repo / f".{PROBE_BUNDLE_NAME}.yaml"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _stringify(value: object) -> str:
    """Flatten an arbitrary hook-emit result into searchable text."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(_stringify(v) for v in value.values())
    if isinstance(value, (list, tuple, set)):
        return "\n".join(_stringify(v) for v in value)
    ci = getattr(value, "context_injection", None)
    if isinstance(ci, str):
        return ci
    return ""


async def render(bundle_path: Path) -> dict:
    from amplifier_foundation.registry import load_bundle

    bundle = await load_bundle(str(bundle_path))
    bundle.load_agent_metadata()
    prepared = await bundle.prepare()
    session = await prepared.create_session(session_cwd=bundle_path.parent)

    out: dict = {}

    tools = session.coordinator.get("tools") or {}
    delegate = tools.get("delegate")
    if delegate is None:
        raise SystemExit("delegate tool did not mount -- cannot render agent catalog")
    desc = delegate.description
    marker = "\nAvailable agents:\n"
    idx = desc.find(marker)
    if idx < 0:
        raise SystemExit("delegate description carries no 'Available agents:' block")
    catalog = desc[idx + 1 :]
    if "No description" in catalog:
        raise SystemExit(
            "agent catalog rendered 'No description' -- agent metadata did not "
            "load; the scratch bundle is not resolving agents/ from the repo"
        )
    out["agent_catalog"] = catalog

    emitted = await session.coordinator.hooks.emit(
        "provider:request",
        {"provider": "kv98-probe", "messages": [], "model": "kv98-probe"},
    )

    haystacks: list[str] = [_stringify(emitted)]
    context = session.coordinator.get("context")
    if context is not None:
        for m in await context.get_messages_for_request():
            if m.get("role") == "system":
                haystacks.append(m.get("content") or "")

    for hay in haystacks:
        found = SKILLS_PATTERN.search(hay)
        if found:
            out["skills_visibility"] = found.group(0)
            return out
    raise SystemExit("skills-visibility block not rendered on any surface")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".", help="repo checkout to measure")
    ap.add_argument("--out", required=True, help="output directory for the capture")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    bundle_path = build_scratch_bundle(repo)
    try:
        rendered = asyncio.run(render(bundle_path))
    finally:
        bundle_path.unlink(missing_ok=True)

    (out / "agent-catalog.txt").write_text(rendered["agent_catalog"], encoding="utf-8")
    (out / "skills-visibility.txt").write_text(rendered["skills_visibility"], encoding="utf-8")

    expected_agents = len(list((repo / "agents").glob("*.md")))
    expected_skills = len(list((repo / "skills").glob("*/SKILL.md")))
    rendered_agents = rendered["agent_catalog"].count("\n  - ")
    rendered_skills = rendered["skills_visibility"].count("\n- **")

    summary = {
        "repo": str(repo),
        "agents_on_disk": expected_agents,
        "agents_rendered": rendered_agents,
        "skills_on_disk": expected_skills,
        "skills_rendered": rendered_skills,
        "agent_catalog_bytes": len(rendered["agent_catalog"].encode("utf-8")),
        "skills_visibility_bytes": len(rendered["skills_visibility"].encode("utf-8")),
    }
    summary["total_bytes"] = summary["agent_catalog_bytes"] + summary["skills_visibility_bytes"]
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))

    if rendered_agents != expected_agents or rendered_skills != expected_skills:
        print(
            "REFUSING: rendered counts do not match what is on disk -- the probe "
            "is measuring the wrong tree.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
