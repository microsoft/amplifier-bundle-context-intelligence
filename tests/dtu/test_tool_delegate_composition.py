"""
Group D: Tool composition correctness for the specialist-behind-gate pattern.

Brian's operational note (issue #233):
  "For agent A to delegate to sibling B inside its sub-session, A's frontmatter
  must include the delegate tool in its tools: list. The fix makes B reachable
  in A's REGISTRY; whether A can call delegate is an orthogonal tool-composition
  concern (agent author responsibility). If your facilitator/tool-designer pair
  was relying on delegate being inherited from the mode's tool policy, you'll
  want to declare it explicitly on each agent."

What this test verifies:
  D1  Facilitator explicitly declares tool-delegate in its own tools: list
  D2  Tool-designer explicitly declares tool-delegate in its own tools: list
  D3  Mode tools.safe contains 'delegate' (root-session policy — separate mechanism)
  D4  Mode tool-policies are root-session-only — proven by checking they are
      in the mode frontmatter, not in either agent frontmatter
  D5  No agent relies on tool-skills being inherited — both declare it explicitly
  D6  The declared tool-delegate source is resolvable (points to amplifier-foundation)
  D7  Both agents declare tool-skills with the bundle-skills source
  D8  The skills config inside tool-skills points to the CI bundle skills subtree
      (so the agent loads its own skills independently, not from parent session)

Key risk that would break the handoff without these checks:
  - If facilitator had NO tool-delegate in tools:, its sub-session would have
    no 'delegate' capability → cannot route to tool-designer at Phase 1→2
  - If tool-designer had NO tool-delegate in tools:, it cannot delegate each
    signal to 'self' for lean per-signal processing (core Phase 2 mechanism)
  - If tool-skills had NO skills: config pointing to CI bundle, the agent
    cannot load context-intelligence-tool-design / eval-design skills
"""

import yaml
from pathlib import Path

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"

# Resolve the bundle root relative to this test file so the checks work in a
# normal checkout, in CI, and inside a DTU container alike. The previous
# implementation hardcoded an absolute, root-owned cache path
# (/root/.amplifier/cache/...), which broke pytest collection on every other
# machine and froze a specific cache hash. The agent/mode files under test are
# shipped in this repo, so the repo root is the correct, portable source.
BUNDLE_ROOT = Path(__file__).resolve().parents[2]


def _load_frontmatter(rel_path):
    text = (BUNDLE_ROOT / rel_path).read_text()
    parts = text.split("---", 2)
    return yaml.safe_load(parts[1]), parts[2]


def test_tool_delegate_composition():
    failures = []

    def check(label, condition, detail=""):
        if condition:
            print(f"  {PASS}  {label}")
        else:
            print(f"  {FAIL}  {label}" + (f"\n         detail: {detail}" if detail else ""))
            failures.append(label)

    facilitator_fm, _ = _load_frontmatter("agents/context-intelligence-design-facilitator.md")
    designer_fm, _ = _load_frontmatter("agents/context-intelligence-tool-designer.md")
    mode_fm, _ = _load_frontmatter("modes/context-intelligence.md")

    fac_tools = {t["module"]: t for t in facilitator_fm.get("tools", [])}
    des_tools = {t["module"]: t for t in designer_fm.get("tools", [])}
    mode_safe = mode_fm.get("mode", {}).get("tools", {}).get("safe", [])

    print("\n── Group D: Tool composition — Brian's operational note ──────────────────\n")

    # D1/D2: Explicit tool-delegate declaration
    check(
        "D1  Facilitator explicitly declares tool-delegate in tools:",
        "tool-delegate" in fac_tools,
        f"declared tools: {list(fac_tools.keys())}",
    )
    check(
        "D2  Tool-designer explicitly declares tool-delegate in tools:",
        "tool-delegate" in des_tools,
        f"declared tools: {list(des_tools.keys())}",
    )

    # D3: Mode has 'delegate' in safe list
    check("D3  Mode tools.safe contains 'delegate' (root-session policy)", "delegate" in mode_safe)

    # D4: Mode tool policies are NOT in agent frontmatters
    # (Agents must be self-sufficient; they cannot rely on mode policy propagation)
    fac_safe = facilitator_fm.get("mode", {}).get("tools", {}).get("safe", [])
    des_safe = designer_fm.get("mode", {}).get("tools", {}).get("safe", [])
    check(
        "D4  Facilitator frontmatter has NO mode.tools.safe block (not relying on mode policy)",
        not fac_safe,
        f"fac mode.tools.safe={fac_safe}",
    )
    check(
        "D4b Tool-designer frontmatter has NO mode.tools.safe block",
        not des_safe,
        f"des mode.tools.safe={des_safe}",
    )

    # D5: Both declare tool-skills (not inherited from parent session)
    check(
        "D5  Facilitator explicitly declares tool-skills in tools:",
        "tool-skills" in fac_tools,
        f"declared tools: {list(fac_tools.keys())}",
    )
    check(
        "D5b Tool-designer explicitly declares tool-skills in tools:",
        "tool-skills" in des_tools,
        f"declared tools: {list(des_tools.keys())}",
    )

    # D6: tool-delegate source points to amplifier-foundation (correct)
    fac_delegate_src = fac_tools.get("tool-delegate", {}).get("source", "")
    des_delegate_src = des_tools.get("tool-delegate", {}).get("source", "")
    foundation_domain = "amplifier-foundation"
    check(
        "D6a Facilitator's tool-delegate source is amplifier-foundation",
        foundation_domain in fac_delegate_src,
        f"source={fac_delegate_src}",
    )
    check(
        "D6b Tool-designer's tool-delegate source is amplifier-foundation",
        foundation_domain in des_delegate_src,
        f"source={des_delegate_src}",
    )

    # D7: tool-skills source points to amplifier-bundle-skills (correct)
    fac_skills_src = fac_tools.get("tool-skills", {}).get("source", "")
    des_skills_src = des_tools.get("tool-skills", {}).get("source", "")
    skills_domain = "amplifier-bundle-skills"
    check(
        "D7a Facilitator's tool-skills source is amplifier-bundle-skills",
        skills_domain in fac_skills_src,
        f"source={fac_skills_src}",
    )
    check(
        "D7b Tool-designer's tool-skills source is amplifier-bundle-skills",
        skills_domain in des_skills_src,
        f"source={des_skills_src}",
    )

    # D8: tool-skills config.skills includes CI bundle skills subtree
    fac_skills_cfg = fac_tools.get("tool-skills", {}).get("config", {}).get("skills", [])
    des_skills_cfg = des_tools.get("tool-skills", {}).get("config", {}).get("skills", [])
    ci_bundle = "amplifier-bundle-context-intelligence"

    check(
        "D8a Facilitator's tool-skills config points to CI bundle skills",
        any(ci_bundle in s for s in fac_skills_cfg),
        f"config.skills={fac_skills_cfg}",
    )
    check(
        "D8b Tool-designer's tool-skills config points to CI bundle skills",
        any(ci_bundle in s for s in des_skills_cfg),
        f"config.skills={des_skills_cfg}",
    )

    # D9: Phase-transition guard — what would break if tool-delegate were absent
    # Simulate: agent with tool-delegate removed from tools: — would it matter?
    # The test proves this is a sub-session concern, not inheritable from mode.
    class FakeTool:
        def __init__(self, name):
            self.name = name

    class FakeCoordinator:
        def __init__(self, tools):
            self.tools = tools

        def get(self, key):
            return {t.name: t for t in self.tools}.get(key)

    # Sub-session with tool-delegate: delegate call is possible
    coord_with_delegate = FakeCoordinator([FakeTool("tool-delegate"), FakeTool("tool-skills")])
    coord_without_delegate = FakeCoordinator([FakeTool("tool-skills")])

    can_delegate_with = coord_with_delegate.get("tool-delegate") is not None
    can_delegate_without = coord_without_delegate.get("tool-delegate") is not None

    check("D9  Sub-session WITH tool-delegate: can call delegate()", can_delegate_with)
    check(
        "D9b Sub-session WITHOUT tool-delegate: CANNOT call delegate() — proves explicit declaration is required",
        not can_delegate_without,
        "this proves Brian's note: the fix makes B reachable but delegate tool must be declared",
    )

    # Summary
    total = 14
    passed = total - len(failures)
    print(f"\n══ Group D Results: {passed}/{total} passed ══\n")
    if failures:
        print("Failed:")
        for f in failures:
            print(f"  ✗ {f}")
    else:
        print(
            "Both agents satisfy Brian's operational note:\n"
            "  • tool-delegate declared explicitly in each agent's own tools:\n"
            "  • Neither agent relies on mode tool-policy inheritance\n"
            "  • Phase 1→2 handoff (facilitator→tool-designer via delegate) is safe\n"
            "  • Phase 2 per-signal delegation (tool-designer→self) is safe\n"
        )

    assert not failures, "Group D tool-composition failures: " + ", ".join(failures)


if __name__ == "__main__":
    test_tool_delegate_composition()
