// Signal: S-14 — single-session variant (returns data blob directly — safe
//         for single-session queries where payload size is bounded)
// Parameters: $session_id
// Output: occurred_at, data_json (Python parses bundle_name, hooks, tools)
MATCH (s:Session {session_id: $session_id})
      -[:SOURCED_FROM]->(e:SessionStartEvent)
WHERE e.data CONTAINS '"bundle_name"'
RETURN e.occurred_at,
       e.data AS data_json
LIMIT 1;
