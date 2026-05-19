// Signal: S-7 — workspace-wide parallel fan-out frequency
// Tier: A — uses DelegateAgentSpawnedEvent via Session anchor
// Parameters: $workspace
// Output: session_id, group_id, agents, fan_out — ordered by fan_out DESC
MATCH (s:Session {workspace: $workspace})
      -[:HAS_EVENT]->(e:DelegateAgentSpawnedEvent)
WHERE e.parallel_group_id IS NOT NULL
WITH s.session_id         AS session_id,
     e.parallel_group_id  AS group_id,
     collect(e.agent)     AS agents,
     count(*)             AS fan_out
WHERE fan_out > 1
RETURN session_id,
       group_id,
       agents,
       fan_out
ORDER BY fan_out DESC, session_id;
