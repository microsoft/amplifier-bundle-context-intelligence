// Signal: S-18 — single-session variant (forward-compatible)
// Parameters: $session_id
// Output: occurred_at, data_json
MATCH (s:Session {session_id: $session_id})
      -[:HAS_EVENT|SOURCED_FROM]->(e)
WHERE e.event_name = 'mentions:resolved'
RETURN e.occurred_at,
       e.data AS data_json
ORDER BY e.occurred_at;
