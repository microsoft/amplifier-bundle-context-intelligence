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
