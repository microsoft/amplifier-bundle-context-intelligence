// Signal: S-13 — mode:context_injected event (fires every LLM turn while a
//               mode is active — strong proxy for "how much was this mode used")
// Tier: A — count only; no JSON extraction; specific label used for performance
// Parameters: $workspace
// Output: session_id, active_turns (descending)
// Post-processing: none — bundle is attributed by joining with S-12 results
// Note: ModeContextInjectedEvent is a specific label; use it, not :Event, to
//       avoid a full event scan
MATCH (s:Session {workspace: $workspace})
      -[:HAS_EVENT]->(e:ModeContextInjectedEvent)
RETURN s.session_id,
       count(*) AS active_turns
ORDER BY active_turns DESC;
