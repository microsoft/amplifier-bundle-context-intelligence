// Signal: S-8 — installed vs used gap (used-bundles side, per session)
// Tier: A for bundle-name retrieval; Tier B for JSON-blob payloads
// Purpose: produce the used_bundle_set for a session, which Python diffs
//          against the workspace registry bundle list to find unused bundles
// Parameters: $session_id
// Output: four parallel lists — Python combines them into used_bundle_set
//
// Column semantics:
//   agent_bundles      — Tier A: namespace prefixes directly readable from agent
//   contrib_tool_names — Tier B: Python maps each name → bundle via tool_to_bundle
//   skill_data_blobs   — Tier B: Python extracts bundle slug from cache path regex
//   mode_data_blobs    — Tier B: Python extracts to_mode → bundle via mode_to_bundle
//
// Note: recipe_path-based bundles (S-3) are NOT included here because recipe_path
//       lives in tool_input JSON and is best handled by the recipes.py module
//       separately — callers should merge S-3 results into used_bundle_set too.
MATCH (s:Session {session_id: $session_id})

// Agents (S-1) — bundle namespace is the left side of 'bundle:component'
OPTIONAL MATCH (s)-[:HAS_EVENT]->(agent_e:DelegateAgentSpawnedEvent)
WHERE agent_e.agent CONTAINS ':'
WITH s, collect(DISTINCT split(agent_e.agent, ':')[0]) AS agent_bundles

// Bundle-contributed tools (S-15) — static lookup maps name → bundle (Python-side)
OPTIONAL MATCH (s)-[:HAS_EVENT]->(tool_e:ToolPreEvent)
WHERE tool_e.tool_name IN [
  'dot_graph',
  'comic_create', 'comic_asset', 'comic_character', 'comic_project', 'comic_style',
  'team_knowledge', 'terminal_inspector',
  'graph_query', 'blob_read',
  'mcp_deepwiki_read_wiki_structure',
  'mcp_deepwiki_read_wiki_contents',
  'mcp_deepwiki_ask_question',
  'generate_image'
]
  OR tool_e.tool_name STARTS WITH 'mcp_'
WITH s, agent_bundles, collect(DISTINCT tool_e.tool_name) AS contrib_tool_names

// Skills lifecycle (S-9) — bundle in cache path; Python regex extracts slug
OPTIONAL MATCH (s)-[:HAS_EVENT|SOURCED_FROM]->(skill_e)
WHERE skill_e.event_name = 'skill:loaded'
WITH s, agent_bundles, contrib_tool_names,
     collect(DISTINCT skill_e.data) AS skill_data_blobs    // Python extracts bundle slug

// Mode activations (S-12) — bundle via lookup; Python parses to_mode
OPTIONAL MATCH (s)-[:HAS_EVENT|SOURCED_FROM]->(mode_e)
WHERE mode_e.event_name = 'mode:changed'
WITH agent_bundles, contrib_tool_names, skill_data_blobs,
     collect(DISTINCT mode_e.data) AS mode_data_blobs      // Python: to_mode → bundle

RETURN agent_bundles,        // Tier A — bundles directly readable
       contrib_tool_names,   // Tier B — Python maps → bundles via tool_to_bundle
       skill_data_blobs,     // Tier B — Python extracts bundle slug from cache path
       mode_data_blobs;      // Tier B — Python extracts mode name → bundle via lookup
