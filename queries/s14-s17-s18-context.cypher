// =============================================================================
// Bundle-Usage Signal Library — Session Context & Mention Queries
// Signals: S-14 (session:start manifest), S-17 (@mention XML), S-18 (future)
// Tiers:
//   B — S-14, S-17: Cypher detects; Python parses large JSON/XML blobs
//   A (future) — S-18: clean structured event; query forward-compatible
// Schema ref: SessionStartEvent.data (JSON blob), Event.event_name,
//             Session -[:SOURCED_FROM]-> SessionStartEvent
// =============================================================================

// -----------------------------------------------------------------------------
// QUERY: s14_session_start_manifest
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
// -----------------------------------------------------------------------------
MATCH (s:Session {workspace: $workspace})
      -[:SOURCED_FROM]->(e:SessionStartEvent)
WHERE e.data CONTAINS '"bundle_name"'      // fast guard: confirms unredacted
RETURN s.session_id,
       e.occurred_at,
       s.node_id AS session_node_id
ORDER BY e.occurred_at DESC;

// -----------------------------------------------------------------------------
// QUERY: s14_session_start_manifest_single
// Signal: S-14 — single-session variant (returns data blob directly — safe
//         for single-session queries where payload size is bounded)
// Parameters: $session_id
// Output: occurred_at, data_json (Python parses bundle_name, hooks, tools)
// -----------------------------------------------------------------------------
MATCH (s:Session {session_id: $session_id})
      -[:SOURCED_FROM]->(e:SessionStartEvent)
WHERE e.data CONTAINS '"bundle_name"'
RETURN e.occurred_at,
       e.data AS data_json
LIMIT 1;

// -----------------------------------------------------------------------------
// QUERY: s14_redaction_rate
// Signal: S-14 diagnostic — what fraction of sessions have unredacted
//         session:start events?
// Purpose: assess S-14 signal coverage before relying on it for reporting
// Parameters: $workspace
// Output: total_sessions, unredacted, unredacted_pct
// No post-processing needed — purely diagnostic
// -----------------------------------------------------------------------------
MATCH (s:Session {workspace: $workspace})
      -[:SOURCED_FROM]->(e:SessionStartEvent)
RETURN count(*) AS total_sessions,
       sum(CASE WHEN e.data CONTAINS '"bundle_name"' THEN 1 ELSE 0 END)
         AS unredacted,
       round(
         100.0
         * sum(CASE WHEN e.data CONTAINS '"bundle_name"' THEN 1 ELSE 0 END)
         / count(*),
         1
       ) AS unredacted_pct;

// -----------------------------------------------------------------------------
// NOTE: s17_mention_xml_candidates
// Signal: S-17 — @mention XML wrappers inside session:start instruction blobs
// Tier: B — XML parsing only; no dedicated Cypher needed
//
// How it works:
//   S-17 is a FALLBACK for when S-18 (mentions:resolved) is not yet emitted.
//   The @mention expansion injects XML like:
//     <context_file paths="@bundle:path → /abs/resolved/path">...</context_file>
//   These are embedded inside the instruction/description fields of the session
//   start data blob — they are not promoted to top-level graph properties.
//
// Cypher role: none beyond what s14_session_start_manifest already provides.
//   Reuse the s14_session_start_manifest result set as input.
//
// Python role:
//   For each session returned by s14_session_start_manifest, Python reads the
//   full data blob and runs an XML parse (or regex) looking for:
//     <context_file paths="@(\S+):(\S+) → (\S+)">
//   Capture group 1 = bundle namespace, group 2 = bundle-relative path.
//
// No additional Cypher file needed — see s14_session_start_manifest above.
// -----------------------------------------------------------------------------

// -----------------------------------------------------------------------------
// QUERY: s18_mentions_resolved
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
// -----------------------------------------------------------------------------
MATCH (s:Session {workspace: $workspace})
      -[:HAS_EVENT|SOURCED_FROM]->(e)
WHERE e.event_name = 'mentions:resolved'
RETURN s.session_id,
       e.occurred_at,
       e.data AS data_json   // Python: parse resolutions[] array for bundle attribution
ORDER BY e.occurred_at DESC;

// -----------------------------------------------------------------------------
// QUERY: s18_mentions_resolved_in_session
// Signal: S-18 — single-session variant (forward-compatible)
// Parameters: $session_id
// Output: occurred_at, data_json
// -----------------------------------------------------------------------------
MATCH (s:Session {session_id: $session_id})
      -[:HAS_EVENT|SOURCED_FROM]->(e)
WHERE e.event_name = 'mentions:resolved'
RETURN e.occurred_at,
       e.data AS data_json
ORDER BY e.occurred_at;
