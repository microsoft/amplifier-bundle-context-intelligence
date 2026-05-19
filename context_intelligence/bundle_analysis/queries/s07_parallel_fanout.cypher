// Signal: S-7 — parallel fan-out detection (concurrent agent spawns)
// Tier: A — parallel_group_id is lifted to top-level on DelegateAgentSpawnedEvent
// Definition: a parallel_group_id shared by >1 spawned events = concurrent fan-out
// Parameters: $session_id
// Output: group_id, agents (list), fan_out, session_id
// Post-processing: Python may split agent strings to extract bundle namespaces
MATCH (s:Session {session_id: $session_id})
      -[:HAS_EVENT]->(e:DelegateAgentSpawnedEvent)
WHERE e.parallel_group_id IS NOT NULL
WITH e.parallel_group_id  AS group_id,
     collect(e.agent)     AS agents,
     count(*)             AS fan_out
WHERE fan_out > 1
RETURN group_id,
       agents,
       fan_out,
       $session_id AS session_id
ORDER BY fan_out DESC;
