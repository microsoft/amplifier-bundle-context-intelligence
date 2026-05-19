// Unified bundle-attribution signals — single session scope.
// Returns rows for four event types; per-row dispatch happens in Python.
// Parameters: $session_id
// Output columns: event_name, agent, tool_name, tool_input_json, data_json
MATCH (s:Session {session_id: $session_id})-[:HAS_EVENT]->(e)
WHERE e:DelegateAgentSpawnedEvent
   OR e.event_name = 'skill:loaded'
   OR (e:ToolPreEvent AND e.tool_name = 'recipes')
   OR e.event_name = 'mentions:resolved'
RETURN
  e.event_name  AS event_name,
  e.agent       AS agent,
  e.tool_name   AS tool_name,
  e.tool_input  AS tool_input_json,
  e.data        AS data_json;
