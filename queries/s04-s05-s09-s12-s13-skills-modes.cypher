// =============================================================================
// Bundle-Usage Signal Library — Skills & Modes Queries
// Signals: S-4 (load_skill), S-5 (mode set), S-9 (skill:loaded lifecycle),
//          S-12 (mode:changed), S-13 (mode:context_injected)
// Tiers:
//   A — S-13: pure Cypher count on :ModeContextInjectedEvent label
//   B — S-4, S-5, S-9, S-12: Cypher detects; Python extracts field + lookup
// Schema ref: ToolPreEvent.tool_name/tool_input, Event.event_name/data,
//             ModeContextInjectedEvent (specific label for S-13 performance)
// =============================================================================

// -----------------------------------------------------------------------------
// QUERY: s04_skill_load_candidates
// Signal: S-4 — skill load via load_skill tool
// Tier: B — skill_name is inside tool_input JSON string; bundle attribution
//           requires a lookup table (lookup/skill_to_bundle.py)
// Parameters: $workspace
// Output: session_id, occurred_at, tool_input_json, tool_call_id
// Post-processing:
//   Python parses tool_input_json to extract the "skill_name" (or "name") key.
//   Python then applies skill_to_bundle lookup dict to get the bundle namespace.
// Performance: anchors on Session (workspace-indexed), then uses ToolPreEvent
//              label + tool_name equality before any property scan
// -----------------------------------------------------------------------------
MATCH (s:Session {workspace: $workspace})
      -[:HAS_EVENT]->(e:ToolPreEvent {tool_name: 'load_skill'})
RETURN s.session_id,
       e.occurred_at,
       e.tool_input   AS tool_input_json,   // Python: extract skill_name key
       e.tool_call_id
ORDER BY e.occurred_at DESC;

// -----------------------------------------------------------------------------
// QUERY: s04_skill_load_in_session
// Signal: S-4 — single-session variant
// Parameters: $session_id
// Output: occurred_at, tool_input_json, tool_call_id
// -----------------------------------------------------------------------------
MATCH (s:Session {session_id: $session_id})
      -[:HAS_EVENT]->(e:ToolPreEvent {tool_name: 'load_skill'})
RETURN e.occurred_at,
       e.tool_input   AS tool_input_json,
       e.tool_call_id
ORDER BY e.occurred_at DESC;

// -----------------------------------------------------------------------------
// QUERY: s09_skill_loaded_lifecycle
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
// -----------------------------------------------------------------------------
MATCH (s:Session {workspace: $workspace})
      -[:HAS_EVENT|SOURCED_FROM]->(e)
WHERE e.event_name = 'skill:loaded'
RETURN s.session_id,
       e.occurred_at,
       e.data AS data_json   // Python: parse data.source field for bundle slug
ORDER BY e.occurred_at DESC;

// -----------------------------------------------------------------------------
// QUERY: s09_skill_loaded_in_session
// Signal: S-9 — single-session variant
// Parameters: $session_id
// Output: occurred_at, data_json
// -----------------------------------------------------------------------------
MATCH (s:Session {session_id: $session_id})
      -[:HAS_EVENT|SOURCED_FROM]->(e)
WHERE e.event_name = 'skill:loaded'
RETURN e.occurred_at,
       e.data AS data_json
ORDER BY e.occurred_at;

// -----------------------------------------------------------------------------
// QUERY: s05_mode_set_candidates
// Signal: S-5 — mode activation via mode tool (tool:pre event)
// Tier: B — mode name is inside tool_input JSON string; bundle via static
//           mode_to_bundle lookup (lookup/mode_to_bundle.py)
// Detection: tool_name='mode' AND tool_input contains '"set"' operation
// Parameters: $workspace
// Output: session_id, occurred_at, tool_input_json
// Post-processing:
//   Python parses tool_input_json to extract "name" key (the mode being set).
//   Python then applies mode_to_bundle lookup dict to get the bundle namespace.
// -----------------------------------------------------------------------------
MATCH (s:Session {workspace: $workspace})
      -[:HAS_EVENT]->(e:ToolPreEvent {tool_name: 'mode'})
WHERE e.tool_input CONTAINS '"set"'
RETURN s.session_id,
       e.occurred_at,
       e.tool_input AS tool_input_json   // Python: extract name key; lookup gives bundle
ORDER BY e.occurred_at DESC;

// -----------------------------------------------------------------------------
// QUERY: s05_mode_set_in_session
// Signal: S-5 — single-session variant
// Parameters: $session_id
// Output: occurred_at, tool_input_json
// -----------------------------------------------------------------------------
MATCH (s:Session {session_id: $session_id})
      -[:HAS_EVENT]->(e:ToolPreEvent {tool_name: 'mode'})
WHERE e.tool_input CONTAINS '"set"'
RETURN e.occurred_at,
       e.tool_input AS tool_input_json
ORDER BY e.occurred_at;

// -----------------------------------------------------------------------------
// QUERY: s12_mode_changed_lifecycle
// Signal: S-12 — mode:changed lifecycle event (richer than S-5: includes
//               from_mode, to_mode, active policies, tool restrictions)
// Tier: B — event_name matched; to_mode and from_mode buried inside data JSON
// Parameters: $workspace
// Output: session_id, occurred_at, data_json
// Post-processing:
//   Python parses data_json to extract "to_mode" and "from_mode" keys.
//   Python applies mode_to_bundle lookup on to_mode → bundle namespace.
//   Rows where to_mode is null/empty indicate a mode clear (not a bundle signal).
// -----------------------------------------------------------------------------
MATCH (s:Session {workspace: $workspace})
      -[:HAS_EVENT|SOURCED_FROM]->(e)
WHERE e.event_name = 'mode:changed'
RETURN s.session_id,
       e.occurred_at,
       e.data AS data_json   // Python: extract to_mode, from_mode keys
ORDER BY e.occurred_at DESC;

// -----------------------------------------------------------------------------
// QUERY: s12_mode_changed_in_session
// Signal: S-12 — single-session variant
// Parameters: $session_id
// Output: occurred_at, data_json
// -----------------------------------------------------------------------------
MATCH (s:Session {session_id: $session_id})
      -[:HAS_EVENT|SOURCED_FROM]->(e)
WHERE e.event_name = 'mode:changed'
RETURN e.occurred_at,
       e.data AS data_json
ORDER BY e.occurred_at;

// -----------------------------------------------------------------------------
// QUERY: s13_mode_active_turns
// Signal: S-13 — mode:context_injected event (fires every LLM turn while a
//               mode is active — strong proxy for "how much was this mode used")
// Tier: A — count only; no JSON extraction; specific label used for performance
// Parameters: $workspace
// Output: session_id, active_turns (descending)
// Post-processing: none — bundle is attributed by joining with S-12 results
// Note: ModeContextInjectedEvent is a specific label; use it, not :Event, to
//       avoid a full event scan
// -----------------------------------------------------------------------------
MATCH (s:Session {workspace: $workspace})
      -[:HAS_EVENT]->(e:ModeContextInjectedEvent)
RETURN s.session_id,
       count(*) AS active_turns
ORDER BY active_turns DESC;

// -----------------------------------------------------------------------------
// QUERY: s13_mode_active_turns_in_session
// Signal: S-13 — single-session variant
// Parameters: $session_id
// Output: active_turns
// -----------------------------------------------------------------------------
MATCH (s:Session {session_id: $session_id})
      -[:HAS_EVENT]->(e:ModeContextInjectedEvent)
RETURN count(*) AS active_turns;

// -----------------------------------------------------------------------------
// QUERY: s13_mode_active_turns_with_context
// Signal: S-13 (extended) — pair mode:changed entry events with the count of
//         mode:context_injected events that followed in the same session
// Tier: B (mode attribution) + A (count)
// Purpose: gives per-activation turn counts, not just session totals — useful
//          to understand multi-mode sessions where modes are switched mid-run
// Parameters: $session_id
// Output: mode_data_json (Python extracts to_mode → bundle), turns_active,
//         entered_at
// Post-processing:
//   Python parses mode_data_json to get to_mode, applies mode_to_bundle lookup.
//   turns_active is the number of LLM turns the mode was in effect (approx).
// Caveat: counts injections after entry time, regardless of subsequent mode
//         changes — a conservative over-count for multi-mode sessions.
// -----------------------------------------------------------------------------
MATCH (s:Session {session_id: $session_id})
      -[:HAS_EVENT|SOURCED_FROM]->(changed)
WHERE changed.event_name = 'mode:changed'
  AND changed.data CONTAINS '"to_mode"'    // entering a mode (not clearing)
WITH s,
     changed,
     datetime(changed.occurred_at) AS entered_at
MATCH (s)-[:HAS_EVENT]->(injected:ModeContextInjectedEvent)
WHERE datetime(injected.occurred_at) > entered_at
WITH changed.data  AS mode_data_json,       // Python extracts to_mode → bundle
     count(injected) AS turns_active,
     entered_at
RETURN mode_data_json,
       turns_active,
       entered_at
ORDER BY entered_at;
