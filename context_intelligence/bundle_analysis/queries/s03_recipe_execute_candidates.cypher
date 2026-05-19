// Signal: S-3 — recipe execution detection
// Tier: B — Cypher detects candidates; Python extracts recipe_path from
//           tool_input JSON string
// Why Tier B: recipe_path is NOT a lifted property — it lives inside tool_input
//             JSON string and cannot be accessed via Cypher property syntax
// Parameters: $workspace
// Output: session_id, occurred_at, tool_input_json (full string), tool_call_id
// Post-processing:
//   Python parses tool_input_json for "recipe_path" key.
//   Typical value: "@bundle-name:path/to/recipe.yaml"
//   Bundle attribution: split on ':' → left side is bundle namespace.
// Performance: anchors on Session (workspace-indexed), then filters on
//              ToolPreEvent label before CONTAINS check
MATCH (s:Session {workspace: $workspace})
      -[:HAS_EVENT]->(e:ToolPreEvent {tool_name: 'recipes'})
WHERE e.tool_input CONTAINS '"operation":"execute"'
  AND e.tool_input CONTAINS '"recipe_path"'
RETURN s.session_id,
       e.occurred_at,
       e.tool_input   AS tool_input_json,
       e.tool_call_id
ORDER BY e.occurred_at DESC;
