// Signal: S-9 — skill:loaded lifecycle event (STRONGEST skill attribution signal)
// Tier: B — event_name matched cleanly; bundle extracted from data.source cache
//           path by Python regex; no lookup table required
// Cache path format: ".amplifier/cache/amplifier-bundle-{slug}-{hash}/..."
// Python extraction: regex r"amplifier-bundle-([^-/]+(?:-[^-/]+)*)-[0-9a-f]{7,}"
//                   on the source field value → capture group 1 = bundle slug
// Parameters: $workspace
// Output: session_id, occurred_at, data_json
// Post-processing:
//   Python parses data_json, extracts the "source" field, applies regex to get
//   the bundle slug.  No lookup table needed — slug IS the bundle namespace.
MATCH (s:Session {workspace: $workspace})
      -[:HAS_EVENT|SOURCED_FROM]->(e)
WHERE e.event_name = 'skill:loaded'
RETURN s.session_id,
       e.occurred_at,
       e.data AS data_json   // Python: parse data.source field for bundle slug
ORDER BY e.occurred_at DESC;
