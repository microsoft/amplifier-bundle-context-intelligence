#!/usr/bin/env python3
"""Render this bundle's always-on skills catalog from a scratch session.

Measures ONE surface, from a real mounted session, with **no LLM call** ($0):
the ``<system-reminder source="hooks-skills-visibility">`` block, as produced by
the skills this repo's own behaviors mount always-on (the navigation + analysis
layers). That block is injected into every session's head on every turn, so its
entry count and byte size are a per-turn tax on every app that loads this bundle.

It also proves the load path for a hidden skill: a skill carrying
``disable-model-invocation: true`` disappears from the "Available skills" index
but must still load by name. The probe calls ``load_skill(skill_name=...)``
through the real mounted tool and records the result, so "still loadable" is a
measurement rather than an assumption.

Mechanism borrowed from lane kv98's ``render_catalog.py`` (same repo, prior
lane). Two details make it work and are easy to get wrong:

* the scratch bundle YAML must sit at the repo root, so relative bundle paths
  resolve against the working tree; and
* a LOCAL skill source must be a bare filesystem path -- ``resolve_skill_source``
  treats anything that is not ``git+``/``https://`` as a path and silently
  returns None for a ``file://`` URI, which falls back to the machine's default
  skills directories and measures the wrong repo entirely.

The skill list is not hardcoded: it is read out of ``behaviors/*.yaml`` and
rewritten from ``git+...@main#subdirectory=skills/X`` to ``<repo>/skills/X``, so
pointing the probe at a BEFORE checkout and an AFTER checkout compares each
tree's real mounted set. Any source that does not exist locally is a hard error.

Usage:
    python docs/lanes/<lane>/render_visibility.py --repo . --out <dir>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

import yaml

FOUNDATION = "git+https://github.com/microsoft/amplifier-foundation@main"
SKILLS_BUNDLE = "git+https://github.com/microsoft/amplifier-bundle-skills@main"
PROBE_BUNDLE_NAME = "ocn2probe"
ENTRY_BEHAVIOR = "behaviors/context-intelligence-analysis.yaml"
HIDDEN_SKILL = "context-intelligence-session-reconstruction"

SKILLS_PATTERN = re.compile(
    r'<system-reminder source="hooks-skills-visibility">.*?</system-reminder>',
    re.S,
)
GIT_SOURCE = re.compile(
    r"^git\+https://github\.com/microsoft/amplifier-bundle-context-intelligence@[^#]+#subdirectory=(.+)$"
)


def collect_mounted_skills(repo: Path, entry: str) -> list[str]:
    """Return the skill source paths a behavior mounts, following its includes.

    Sources are returned as repo-relative paths (e.g. ``skills/foo``), in mount
    order, de-duplicated -- mirroring the list-merge-with-dedup the foundation
    performs when composing layered behaviors.
    """
    seen_files: set[str] = set()
    out: list[str] = []

    def walk(rel: str) -> None:
        if rel in seen_files:
            return
        seen_files.add(rel)
        data = yaml.safe_load((repo / rel).read_text(encoding="utf-8")) or {}
        for inc in data.get("includes") or []:
            ref = inc.get("bundle") if isinstance(inc, dict) else inc
            if isinstance(ref, str) and ref.startswith("context-intelligence:behaviors/"):
                walk(ref.split(":", 1)[1] + ".yaml")
        for tool in data.get("tools") or []:
            if not isinstance(tool, dict) or tool.get("module") != "tool-skills":
                continue
            for src in (tool.get("config") or {}).get("skills") or []:
                match = GIT_SOURCE.match(str(src))
                if not match:
                    raise SystemExit(f"unrecognized skill source in {rel}: {src}")
                sub = match.group(1)
                if sub not in out:
                    out.append(sub)

    walk(entry)
    return out


def build_scratch_bundle(repo: Path, skill_subdirs: list[str]) -> Path:
    """Write a scratch bundle YAML at the repo root wiring LOCAL skill sources."""
    lines = [
        "bundle:",
        f"  name: {PROBE_BUNDLE_NAME}",
        "  version: 0.0.0",
        "  description: Scratch bundle for lane ocn2 skills-catalog measurement.",
        "",
        "session:",
        "  orchestrator:",
        "    module: loop-basic",
        "    source: git+https://github.com/microsoft/amplifier-module-loop-basic@main",
        "  context:",
        "    module: context-simple",
        "    source: git+https://github.com/microsoft/amplifier-module-context-simple@main",
        "",
        "tools:",
        "  - module: tool-skills",
        f"    source: {SKILLS_BUNDLE}#subdirectory=modules/tool-skills",
        "    config:",
        "      visibility:",
        "        placement: request",
        "      skills:",
    ]
    for sub in skill_subdirs:
        local = (repo / sub).resolve()
        if not (local / "SKILL.md").is_file():
            raise SystemExit(f"mounted skill source has no SKILL.md: {local}")
        lines.append(f'        - "{local}"')
    lines.append("")

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
    prepared = await bundle.prepare()
    session = await prepared.create_session(session_cwd=bundle_path.parent)

    out: dict = {}

    emitted = await session.coordinator.hooks.emit(
        "provider:request",
        {"provider": "ocn2-probe", "messages": [], "model": "ocn2-probe"},
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
            break
    else:
        raise SystemExit("skills-visibility block not rendered on any surface")

    # Prove the hidden skill still loads by name through the real tool.
    tools = session.coordinator.get("tools") or {}
    tool = tools.get("load_skill")
    if tool is None:
        raise SystemExit("load_skill tool did not mount")
    result = await tool.execute({"skill_name": HIDDEN_SKILL})
    payload = getattr(result, "output", None)
    body = ""
    if isinstance(payload, dict):
        body = str(payload.get("content") or payload.get("skill_content") or "")
        if not body:
            body = json.dumps(payload)
    elif payload is not None:
        body = str(payload)
    out["load_skill"] = {
        "skill_name": HIDDEN_SKILL,
        "error": getattr(result, "error", None),
        "loaded_chars": len(body),
        "head": body[:400],
    }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".", help="repo checkout to measure")
    ap.add_argument("--out", required=True, help="output directory for the capture")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    subdirs = collect_mounted_skills(repo, ENTRY_BEHAVIOR)
    bundle_path = build_scratch_bundle(repo, subdirs)
    try:
        rendered = asyncio.run(render(bundle_path))
    finally:
        bundle_path.unlink(missing_ok=True)

    block = rendered["skills_visibility"]
    (out / "skills-visibility.txt").write_text(block, encoding="utf-8")
    (out / "load-skill-check.json").write_text(
        json.dumps(rendered["load_skill"], indent=2), encoding="utf-8"
    )

    head, _, tail = block.partition("User-invoked skills (available via /command):")
    summary = {
        "repo": str(repo),
        "mounted_skill_sources": subdirs,
        "mounted_count": len(subdirs),
        "visible_skills": head.count("\n- **"),
        "user_invoked_skills": tail.count("\n- **"),
        "visibility_block_bytes": len(block.encode("utf-8")),
        "hidden_skill_loads": (
            rendered["load_skill"]["error"] is None and rendered["load_skill"]["loaded_chars"] > 0
        ),
        "hidden_skill_loaded_chars": rendered["load_skill"]["loaded_chars"],
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))

    rendered_total = summary["visible_skills"] + summary["user_invoked_skills"]
    if rendered_total != summary["mounted_count"]:
        print(
            f"REFUSING: {rendered_total} skills rendered but {summary['mounted_count']} "
            "mounted -- the probe is measuring the wrong tree.",
            file=sys.stderr,
        )
        return 2
    if not summary["hidden_skill_loads"]:
        print(
            f"REFUSING: {HIDDEN_SKILL} did not load by name.",
            file=sys.stderr,
        )
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
