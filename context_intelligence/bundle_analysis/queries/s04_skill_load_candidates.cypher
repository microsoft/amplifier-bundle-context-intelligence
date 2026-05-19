// Signal: S-4 — skill load via load_skill tool
// Tier: B — skill_name is inside tool_input JSON string; bundle attribution
//           requires a lookup table (lookup/skill_to_bundle.py)
// Parameters: $workspace
// Output: session_id, occurred_at, tool_input_json, tool_call_id
// Post-processing:
//   Python parses tool_input_json to extract the "skill_name" (or "name") key.
//   Python then applies skill_to_bundle lookup dict to get the bundle namespace.
// Performance: anchors on Session (workspace-indexed), then uses ToolPreEvent
//              label + tool_name equality before any property scan
MATCH (s:Session {workspace: $workspace})
      -[:HAS_EVENT]->(e:ToolPreEvent {tool_name: 'load_skill'})
RETURN s.session_id,
       e.occurred_at,
       e.tool_input   AS tool_input_json,   // Python: extract skill_name key
       e.tool_call_id
ORDER BY e.occurred_at DESC;
