// Signal: S-10 — recipe lifecycle events
// Tier: B — event_name matched; recipe_path exists only in recipe:start rows
//           (inside data JSON string); other event types require joining back
//           to the recipe:start event of the same session
// Parameters: $workspace
// Output: event_name, session_id, occurred_at, data_json
// Post-processing:
//   For recipe:start rows: Python parses data_json for "recipe_path" key → bundle.
//   For recipe:step/complete/loop rows: Python joins session_id back to the
//   corresponding recipe:start row to inherit the recipe_path.
// Note: event_count_in_session is a window aggregate — use with caution on
//       large result sets; LIMIT 200 is a safety guard
MATCH (s:Session {workspace: $workspace})
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
       s.session_id,
       e.occurred_at,
       e.data AS data_json   // Python extracts recipe_path from recipe:start rows only
ORDER BY e.occurred_at DESC
LIMIT 200;
