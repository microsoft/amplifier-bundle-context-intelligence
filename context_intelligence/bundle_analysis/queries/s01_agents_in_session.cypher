// Signal: S-1 — Agent delegation (bundle namespace), single session
// Tier: A — pure Cypher, agent is a lifted top-level property
// Performance: starts from Session (indexed on workspace + session_id); traverses
//              only DelegateAgentSpawnedEvent label — avoids full :Event scan
// Parameters: $session_id
// Output: bundle, component, invocation_count, failure_count
// Post-processing: none
MATCH (s:Session {session_id: $session_id})
      -[:HAS_EVENT]->(e:DelegateAgentSpawnedEvent)
WHERE e.agent CONTAINS ':'
WITH s,
     split(e.agent, ':')[0] AS bundle,
     split(e.agent, ':')[1] AS component,
     e.tool_call_id          AS call_id
OPTIONAL MATCH (s)-[:HAS_EVENT]->(err:DelegateErrorEvent {tool_call_id: call_id})
RETURN bundle,
       component,
       count(DISTINCT call_id)          AS invocations,
       count(DISTINCT err.tool_call_id) AS failures
ORDER BY invocations DESC;
