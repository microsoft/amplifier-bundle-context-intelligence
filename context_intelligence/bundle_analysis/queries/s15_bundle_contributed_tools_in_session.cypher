// Signal: S-15 — single-session variant
// Parameters: $session_id
// Output: tool_name, invocations
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
