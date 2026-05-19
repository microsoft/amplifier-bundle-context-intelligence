// =============================================================================
// Bundle-Usage Signal Library — Recipe Queries
// Signals: S-3 (recipe execute), S-10 (recipe lifecycle), S-11 (resume gap)
// Tier: B — Cypher detects candidates; Python extracts recipe_path from
//           tool_input / data JSON strings (no APOC, no JSON path access)
// Schema ref: ToolPreEvent.tool_name, ToolPreEvent.tool_input (JSON string),
//             Event.event_name, Event.data (JSON string)
// =============================================================================

// -----------------------------------------------------------------------------
// QUERY: s03_recipe_execute_candidates
// Signal: S-3 — recipe execution detection
// Tier: B — Cypher detects candidates; Python extracts recipe_path from
//           tool_input JSON string
// Why Tier B: recipe_path is NOT a lifted property — it lives inside tool_input
//             JSON string and cannot be accessed via Cypher property syntax
// Parameters: $workspace
// Output: session_id, occurred_at, tool_input_json (full string), tool_call_id
// Post-processing:
//   Python parses tool_input_json for "recipe_path" key.
//   Typical value: "@bundle-name:path/to/recipe.yaml"
//   Bundle attribution: split on ':' → left side is bundle namespace.
// Performance: anchors on Session (workspace-indexed), then filters on
//              ToolPreEvent label before CONTAINS check
// -----------------------------------------------------------------------------
MATCH (s:Session {workspace: $workspace})
      -[:HAS_EVENT]->(e:ToolPreEvent {tool_name: 'recipes'})
WHERE e.tool_input CONTAINS '"operation":"execute"'
  AND e.tool_input CONTAINS '"recipe_path"'
RETURN s.session_id,
       e.occurred_at,
       e.tool_input   AS tool_input_json,
       e.tool_call_id
ORDER BY e.occurred_at DESC;

// -----------------------------------------------------------------------------
// QUERY: s03_recipe_execute_in_session
// Signal: S-3 — single-session recipe execution (filtered variant)
// Parameters: $session_id
// Output: occurred_at, tool_input_json, tool_call_id
// -----------------------------------------------------------------------------
MATCH (s:Session {session_id: $session_id})
      -[:HAS_EVENT]->(e:ToolPreEvent {tool_name: 'recipes'})
WHERE e.tool_input CONTAINS '"operation":"execute"'
  AND e.tool_input CONTAINS '"recipe_path"'
RETURN e.occurred_at,
       e.tool_input   AS tool_input_json,
       e.tool_call_id
ORDER BY e.occurred_at DESC;

// -----------------------------------------------------------------------------
// QUERY: s10_recipe_lifecycle
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
// -----------------------------------------------------------------------------
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

// -----------------------------------------------------------------------------
// QUERY: s10_recipe_lifecycle_in_session
// Signal: S-10 — single-session recipe lifecycle variant
// Parameters: $session_id
// Output: event_name, occurred_at, data_json
// -----------------------------------------------------------------------------
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

// -----------------------------------------------------------------------------
// QUERY: s11_resume_attribution_gap — Step 1
// Signal: S-11 — recipe resume attribution gap (Step 1: find all resume calls)
// Tier: B + cross-session join
// Problem: recipes(operation='resume') carries only a session_id parameter (the
//          recipe session to resume), NOT the recipe_path. The recipe_path only
//          exists in the original recipe:start event of that session.
// Step 1: Find all resume calls and return the raw tool_input for Python
//         to extract the target session_id from the JSON string.
// Parameters: $workspace
// Output: resume_session_id, resume_input_json, resumed_at
// Post-processing (Step 1):
//   Python parses resume_input_json to extract the "session_id" parameter value.
//   That value is then used as $recipe_session_id in Step 2 below.
// -----------------------------------------------------------------------------
MATCH (s:Session {workspace: $workspace})
      -[:HAS_EVENT]->(e:ToolPreEvent {tool_name: 'recipes'})
WHERE e.tool_input CONTAINS '"operation":"resume"'
WITH s.session_id  AS resume_session_id,
     e.tool_input  AS resume_input_json,   // Python extracts session_id param
     e.occurred_at AS resumed_at
RETURN resume_session_id,
       resume_input_json,
       resumed_at
ORDER BY resumed_at DESC
LIMIT 100;

// -----------------------------------------------------------------------------
// QUERY: s11_find_original_recipe_for_session — Step 2
// Signal: S-11 — recipe resume attribution gap (Step 2: find originating recipe)
// Tier: B + cross-session join
// Purpose: given the recipe session_id extracted from a resume call's tool_input,
//          find the recipe:start event in that session to recover the recipe_path
// Parameters: $recipe_session_id (extracted by Python from resume_input_json)
// Output: data_json (Python extracts recipe_path → bundle), occurred_at, session_id
// Post-processing:
//   Python parses data_json for "recipe_path" → extracts "@bundle:" prefix → bundle.
//   If the session has no recipe:start, the recipe_path is irrecoverably unknown.
// -----------------------------------------------------------------------------
MATCH (s:Session {session_id: $recipe_session_id})
      -[:HAS_EVENT|SOURCED_FROM]->(e)
WHERE e.event_name = 'recipe:start'
RETURN e.data    AS data_json,   // Python extracts recipe_path → bundle
       e.occurred_at,
       s.session_id
LIMIT 1;
