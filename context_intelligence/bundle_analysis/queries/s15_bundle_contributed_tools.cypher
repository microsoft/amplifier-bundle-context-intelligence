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
