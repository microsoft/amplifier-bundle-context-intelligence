// Signal: S-10 — single-session recipe lifecycle variant
// Parameters: $session_id
// Output: event_name, occurred_at, data_json
MATCH (s:Session {session_id: $session_id})
      -[:HAS_EVENT|SOURCED_FROM]->(e)
WHERE e.event_name IN [
  'recipe:start',
  'recipe:step',
  'recipe:complete',
  'recipe:approval',
  'recipe:loop_iteration',
  'recipe:loop_complete'
]
RETURN e.event_name,
       e.occurred_at,
       e.data AS data_json
ORDER BY e.occurred_at;
