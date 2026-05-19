// Signal: S-15 maintenance — detect tool names not in the known set
// Purpose: surface newly added bundle-contributed tools not yet in the lookup
//          table.  Run periodically to keep tool_to_bundle.py current.
// Parameters: $workspace
// Output: unknown_tool_name (DISTINCT), occurrences
// Action: add any returned tool_name to lookup/tool_to_bundle.py and to the
//         IN list in s15_bundle_contributed_tools above
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
