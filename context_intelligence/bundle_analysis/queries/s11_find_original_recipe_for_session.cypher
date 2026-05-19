// Signal: S-11 — recipe resume attribution gap (Step 2: find originating recipe)
// Tier: B + cross-session join
// Purpose: given the recipe session_id extracted from a resume call's tool_input,
//          find the recipe:start event in that session to recover the recipe_path
// Parameters: $recipe_session_id (extracted by Python from resume_input_json)
// Output: data_json (Python extracts recipe_path → bundle), occurred_at, session_id
// Post-processing:
//   Python parses data_json for "recipe_path" → extracts "@bundle:" prefix → bundle.
//   If the session has no recipe:start, the recipe_path is irrecoverably unknown.
MATCH (s:Session {session_id: $recipe_session_id})
      -[:HAS_EVENT|SOURCED_FROM]->(e)
WHERE e.event_name = 'recipe:start'
RETURN e.data    AS data_json,   // Python extracts recipe_path → bundle
       e.occurred_at,
       s.session_id
LIMIT 1;
