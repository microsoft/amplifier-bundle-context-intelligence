// Signal: S-13 — single-session variant
// Parameters: $session_id
// Output: active_turns
MATCH (s:Session {session_id: $session_id})
      -[:HAS_EVENT]->(e:ModeContextInjectedEvent)
RETURN count(*) AS active_turns;
