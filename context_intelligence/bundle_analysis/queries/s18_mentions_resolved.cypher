// Signal: S-18 — mentions:resolved event
// Tier: A — structured event; bundle is a direct field in resolutions[]
// In flight: PR #213 (microsoft/amplifier-foundation) is open with 42 passing tests.
// bundle_context source only in this PR; user_prompt source deferred to follow-up.
// Schema confirmed — bundle is a direct field in resolutions[], no string extraction needed.
// This query will start returning results once PR #213 merges.
// Parameters: $workspace
// Output: session_id, occurred_at, data_json
// Post-processing (after event lands):
//   Python parses data_json for "resolutions" array; each element has:
//     - mention: the original @bundle:path string
//     - bundle:  the resolved bundle namespace (top-level after landing)
//     - resolved_path: absolute filesystem path
MATCH (s:Session {workspace: $workspace})
      -[:HAS_EVENT|SOURCED_FROM]->(e)
WHERE e.event_name = 'mentions:resolved'
RETURN s.session_id,
       e.occurred_at,
       e.data AS data_json   // Python: parse resolutions[] array for bundle attribution
ORDER BY e.occurred_at DESC;
