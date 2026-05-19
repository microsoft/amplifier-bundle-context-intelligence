// Signal: S-13 (extended) — pair mode:changed entry events with the count of
//         mode:context_injected events that followed in the same session
// Tier: B (mode attribution) + A (count)
// Purpose: gives per-activation turn counts, not just session totals — useful
//          to understand multi-mode sessions where modes are switched mid-run
// Parameters: $session_id
// Output: mode_data_json (Python extracts to_mode → bundle), turns_active,
//         entered_at
// Post-processing:
//   Python parses mode_data_json to get to_mode, applies mode_to_bundle lookup.
//   turns_active is the number of LLM turns the mode was in effect (approx).
// Caveat: counts injections after entry time, regardless of subsequent mode
//         changes — a conservative over-count for multi-mode sessions.
MATCH (s:Session {session_id: $session_id})
      -[:HAS_EVENT|SOURCED_FROM]->(changed)
WHERE changed.event_name = 'mode:changed'
  AND changed.data CONTAINS '"to_mode"'    // entering a mode (not clearing)
WITH s,
     changed,
     datetime(changed.occurred_at) AS entered_at
MATCH (s)-[:HAS_EVENT]->(injected:ModeContextInjectedEvent)
WHERE datetime(injected.occurred_at) > entered_at
WITH changed.data  AS mode_data_json,       // Python extracts to_mode → bundle
     count(injected) AS turns_active,
     entered_at
RETURN mode_data_json,
       turns_active,
       entered_at
ORDER BY entered_at;
