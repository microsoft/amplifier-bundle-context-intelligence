// Signal: S-4 — single-session variant
// Parameters: $session_id
// Output: occurred_at, tool_input_json, tool_call_id
MATCH (s:Session {session_id: $session_id})
      -[:HAS_EVENT]->(e:ToolPreEvent {tool_name: 'load_skill'})
RETURN e.occurred_at,
       e.tool_input   AS tool_input_json,
       e.tool_call_id
ORDER BY e.occurred_at DESC;
