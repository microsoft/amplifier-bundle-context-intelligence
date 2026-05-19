// Signal: S-5 — single-session variant
// Parameters: $session_id
// Output: occurred_at, tool_input_json
MATCH (s:Session {session_id: $session_id})
      -[:HAS_EVENT]->(e:ToolPreEvent {tool_name: 'mode'})
WHERE e.tool_input CONTAINS '"set"'
RETURN e.occurred_at,
       e.tool_input AS tool_input_json
ORDER BY e.occurred_at;
