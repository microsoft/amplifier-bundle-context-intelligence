---
mode:
  name: bundle-usage
  description: >
    Analyse what bundles and components a session or workspace actually used
    versus what was contributed. Surfaces gap analysis and improvement
    suggestions (tree-shake, mode-refactor, config-gap, engagement gap).
  advertised: false
  default_action: block

  contributes:
    agents:
      # Keys MUST be fully namespaced (namespace:name) to match the agent_name
      # used by the delegate tool and session_spawner.py lookup.
      context-intelligence:bundle-usage-analyst:
        source: "@context-intelligence:agents/bundle-usage-analyst"
    tools:
      - module: tool-bundle-usage
        source: git+https://github.com/microsoft/amplifier-bundle-context-intelligence@main#subdirectory=modules/tool-bundle-usage

  tools:
    safe:
      - bundle_usage
      - read_file
      - write_file
      - glob
      - grep
      - bash
      - delegate
      - todo
    warn:
      - edit_file
---

# Bundle Usage Mode

This mode gates the bundle usage analysis capability. When inactive, the `bundle_usage` tool and `bundle-usage-analyst` agent are NOT loaded — zero footprint. When activated (`mode(operation="set", name="bundle-usage")` or `/bundle-usage` CLI prefix), the tool and agent are contributed and the analyst becomes the default destination for usage questions.

## Activation

Two activation paths:

| Path | Command |
|------|---------|
| Tool-based (always works) | `mode(operation="set", name="bundle-usage")` |
| CLI shortcut prefix       | `/bundle-usage <prompt>` (requires amplifier-bundle-modes PR #21 merged) |

## Mandatory Routing

**When the mode is active and the user asks any question about bundle usage, what was used vs declared, tree-shake candidates, or improvement suggestions — delegate IMMEDIATELY to `context-intelligence:bundle-usage-analyst` with `context_depth="none"`.** The analyst owns the workflow: call `bundle_usage`, optionally read content files for engagement reasoning, write the report.

## Tool Policy

`bundle_usage`, `read_file`, `write_file`, `glob`, `grep`, `bash`, `delegate`, `todo` are auto-approved. `edit_file` is allowed but warns — this mode produces reports, it does not modify source files.

## Exit

Clear the mode (`mode(operation="clear")`) when you no longer need bundle usage tooling. The analyst and tool are removed from subsequent sessions; the report file you wrote remains on disk.
