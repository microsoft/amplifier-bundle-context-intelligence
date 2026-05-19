// Signal: S-12 — mode:changed lifecycle event (richer than S-5: includes
//               from_mode, to_mode, active policies, tool restrictions)
// Tier: B — event_name matched; to_mode and from_mode buried inside data JSON
// Parameters: $workspace
// Output: session_id, occurred_at, data_json
// Post-processing:
//   Python parses data_json to extract "to_mode" and "from_mode" keys.
//   Python applies mode_to_bundle lookup on to_mode → bundle namespace.
//   Rows where to_mode is null/empty indicate a mode clear (not a bundle signal).
MATCH (s:Session {workspace: $workspace})
      -[:HAS_EVENT|SOURCED_FROM]->(e)
WHERE e.event_name = 'mode:changed'
RETURN s.session_id,
       e.occurred_at,
       e.data AS data_json   // Python: extract to_mode, from_mode keys
ORDER BY e.occurred_at DESC;
