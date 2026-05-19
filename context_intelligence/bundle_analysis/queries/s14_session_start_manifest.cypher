// Signal: S-14 — session:start.data as bundle manifest
// Tier: B — event detected cleanly via CONTAINS guard; data blob is multi-KB
//           and must be parsed in Python, not in Cypher
// WARNING: data is redacted in ~65% of sessions (hooks-redaction strips PII /
//          large payloads).  The CONTAINS '"bundle_name"' guard filters to only
//          unredacted rows, avoiding wasted Python round-trips.
// Parameters: $workspace
// Output: session_id, occurred_at, session_node_id
// Post-processing:
//   Python reads events.jsonl for each returned session_id and locates the
//   session:start event line.  From that line's data blob, Python extracts:
//     - data.bundle_name    → primary bundle namespace
//     - data.hooks[].module → additional bundle signals (hook modules)
//     - data.tools[].name   → tool list (cross-ref with tool_to_bundle lookup)
// SAFETY: do NOT return e.data in large result sets — the blob can be >50 KB.
//         Return only the session identifiers for Python-side retrieval.
MATCH (s:Session {workspace: $workspace})
      -[:SOURCED_FROM]->(e:SessionStartEvent)
WHERE e.data CONTAINS '"bundle_name"'      // fast guard: confirms unredacted
RETURN s.session_id,
       e.occurred_at,
       s.node_id AS session_node_id
ORDER BY e.occurred_at DESC;
