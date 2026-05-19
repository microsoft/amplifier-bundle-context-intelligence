// =============================================================================
// Bundle-Usage Signal Library — Coverage Gap & Bundle-Contributed Tools
// Signals: S-8 (installed vs used gap), S-15 (bundle-contributed tools)
// Tier: A — all fields lifted; tool_name is a top-level property on ToolPreEvent
// Schema ref: ToolPreEvent.tool_name, Session -[:HAS_EVENT]-> ToolPreEvent,
//             DelegateAgentSpawnedEvent.agent, Event.event_name
// =============================================================================

// -----------------------------------------------------------------------------
// QUERY: s15_bundle_contributed_tools
// Signal: S-15 — bundle-contributed tool invocations across a workspace
// Tier: A — tool_name is a lifted top-level property; IN check is pure Cypher
// Bundle attribution: baked in via static mapping (lookup/tool_to_bundle.py)
// Parameters: $workspace
// Output: tool_name, invocations, session_count
// Post-processing: Python maps tool_name → bundle via tool_to_bundle lookup
//
// EXCLUDED (built-in foundation tools):
//   bash, read_file, write_file, edit_file, grep, glob, delegate, recipes,
//   load_skill, mode, python_check, LSP, web_search, web_fetch,
//   perplexity_research, containers, apply_patch, todo, generate_image
//
// INCLUDED (known bundle-contributed tools as of schema freeze):
//   dot_graph                                      → dot-graph bundle
//   comic_create, comic_asset, comic_character,
//     comic_project, comic_style                   → comic bundle
//   team_knowledge                                 → team-knowledge bundle
//   terminal_inspector                             → terminal-inspector bundle
//   graph_query, blob_read                         → ci / graph bundle
//   mcp_deepwiki_*                                 → deepwiki MCP bundle
//   mcp_* (prefix catch-all)                       → any future MCP bundle
// -----------------------------------------------------------------------------
MATCH (s:Session {workspace: $workspace})
      -[:HAS_EVENT]->(e:ToolPreEvent)
WHERE e.tool_name IN [
  'dot_graph',
  'comic_create', 'comic_asset', 'comic_character', 'comic_project', 'comic_style',
  'team_knowledge',
  'terminal_inspector',
  'graph_query', 'blob_read',
  'mcp_deepwiki_read_wiki_structure',
  'mcp_deepwiki_read_wiki_contents',
  'mcp_deepwiki_ask_question',
  'generate_image'
]
  OR e.tool_name STARTS WITH 'mcp_'    // catch any future mcp_* tools
RETURN e.tool_name,
       count(*)                        AS invocations,
       count(DISTINCT s.session_id)    AS session_count
ORDER BY invocations DESC;

// -----------------------------------------------------------------------------
// QUERY: s15_bundle_contributed_tools_in_session
// Signal: S-15 — single-session variant
// Parameters: $session_id
// Output: tool_name, invocations
// -----------------------------------------------------------------------------
MATCH (s:Session {session_id: $session_id})
      -[:HAS_EVENT]->(e:ToolPreEvent)
WHERE e.tool_name IN [
  'dot_graph',
  'comic_create', 'comic_asset', 'comic_character', 'comic_project', 'comic_style',
  'team_knowledge',
  'terminal_inspector',
  'graph_query', 'blob_read',
  'mcp_deepwiki_read_wiki_structure',
  'mcp_deepwiki_read_wiki_contents',
  'mcp_deepwiki_ask_question',
  'generate_image'
]
  OR e.tool_name STARTS WITH 'mcp_'
RETURN e.tool_name,
       count(*) AS invocations
ORDER BY invocations DESC;

// -----------------------------------------------------------------------------
// QUERY: s15_unknown_tools
// Signal: S-15 maintenance — detect tool names not in the known set
// Purpose: surface newly added bundle-contributed tools not yet in the lookup
//          table.  Run periodically to keep tool_to_bundle.py current.
// Parameters: $workspace
// Output: unknown_tool_name (DISTINCT), occurrences
// Action: add any returned tool_name to lookup/tool_to_bundle.py and to the
//         IN list in s15_bundle_contributed_tools above
// -----------------------------------------------------------------------------
MATCH (s:Session {workspace: $workspace})
      -[:HAS_EVENT]->(e:ToolPreEvent)
WHERE NOT e.tool_name IN [
  // built-in foundation tools
  'bash', 'read_file', 'write_file', 'edit_file', 'grep', 'glob',
  'delegate', 'recipes', 'load_skill', 'mode', 'python_check', 'LSP',
  'web_search', 'web_fetch', 'perplexity_research', 'containers',
  'apply_patch', 'todo', 'generate_image',
  // known bundle-contributed tools
  'dot_graph',
  'comic_create', 'comic_asset', 'comic_character', 'comic_project', 'comic_style',
  'team_knowledge',
  'terminal_inspector',
  'graph_query', 'blob_read',
  'mcp_deepwiki_read_wiki_structure',
  'mcp_deepwiki_read_wiki_contents',
  'mcp_deepwiki_ask_question'
]
  AND NOT e.tool_name STARTS WITH 'mcp_'
RETURN DISTINCT e.tool_name AS unknown_tool_name,
               count(*)     AS occurrences
ORDER BY occurrences DESC;

// -----------------------------------------------------------------------------
// QUERY: s08_used_bundles_in_session
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
// -----------------------------------------------------------------------------
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

// =============================================================================
// USAGE NOTE (S-8 workflow):
//
//   1. Run s08_used_bundles_in_session (or s01_agents_cross_session for workspace)
//      to build the used_bundle_set.
//   2. Load the installed bundle list from the workspace registry
//      (registry.json or `amplifier bundles list --json`).
//   3. Python: installed_set - used_bundle_set = unused (coverage gap).
//   4. Python: used_bundle_set - installed_set = unknown (might indicate removed
//      or renamed bundles — worth investigating).
//
//   For workspace-level S-8, iterate over all sessions returned by a workspace
//   query and union the per-session used_bundle_sets, then diff once at the end.
// =============================================================================
