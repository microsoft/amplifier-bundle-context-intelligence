// Signal: S-5 — mode activation via mode tool (tool:pre event)
// Tier: B — mode name is inside tool_input JSON string; bundle via static
//           mode_to_bundle lookup (lookup/mode_to_bundle.py)
// Detection: tool_name='mode' AND tool_input contains '"set"' operation
// Parameters: $workspace
// Output: session_id, occurred_at, tool_input_json
// Post-processing:
//   Python parses tool_input_json to extract "name" key (the mode being set).
//   Python then applies mode_to_bundle lookup dict to get the bundle namespace.
MATCH (s:Session {workspace: $workspace})
      -[:HAS_EVENT]->(e:ToolPreEvent {tool_name: 'mode'})
WHERE e.tool_input CONTAINS '"set"'
RETURN s.session_id,
       e.occurred_at,
       e.tool_input AS tool_input_json   // Python: extract name key; lookup gives bundle
