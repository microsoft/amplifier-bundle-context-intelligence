// =============================================================================
// Bundle-Usage Signal Library — Agent Delegation & Timing Queries
// Signals: S-1 (agent delegation), S-2 (agent timing), S-7 (parallel fanout)
// Tier: A — pure Cypher; all fields lifted to top-level graph properties
// Schema ref: Delegation.agent, Delegation.started_at/ended_at,
//             DelegateAgentSpawnedEvent.parallel_group_id
// =============================================================================

// -----------------------------------------------------------------------------
// QUERY: s01_agents_in_session
// Signal: S-1 — Agent delegation (bundle namespace), single session
// Tier: A — pure Cypher, agent is a lifted top-level property
// Performance: starts from Session (indexed on workspace + session_id); traverses
//              only DelegateAgentSpawnedEvent label — avoids full :Event scan
// Parameters: $session_id
// Output: bundle, component, invocation_count, failure_count
// Post-processing: none
// -----------------------------------------------------------------------------
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

// -----------------------------------------------------------------------------
// QUERY: s01_agents_cross_session
// Signal: S-1 — cross-session frequency ranking across a whole workspace
// Tier: A — uses Delegation SST node (workspace-scoped, no Session traversal needed)
// Performance: Delegation.workspace is a property on a label-scanned node type;
//              acceptable for workspace-scoped cross-session aggregation
// Parameters: $workspace
// Output: bundle, component, total_invocations, session_count, success_pct
// Post-processing: none
// -----------------------------------------------------------------------------
MATCH (d:Delegation {workspace: $workspace})
WHERE d.agent CONTAINS ':'
WITH split(d.agent, ':')[0] AS bundle,
     split(d.agent, ':')[1] AS component,
     d.success               AS succeeded,
     d.session_id            AS session_id
RETURN bundle,
       component,
       count(*)                                                       AS total_invocations,
       count(DISTINCT session_id)                                     AS session_count,
       round(100.0 * sum(CASE WHEN succeeded THEN 1 ELSE 0 END)
             / count(*), 1)                                           AS success_pct
ORDER BY total_invocations DESC;

// -----------------------------------------------------------------------------
// QUERY: s02_agent_timing
// Signal: S-2 — agent duration per bundle/component (Delegation SST node carries
//               started_at / ended_at as lifted top-level datetime strings)
// Tier: A — pure Cypher, timing fields are lifted to Delegation node
// Parameters: $workspace, $session_id
// Output: bundle, component, avg_ms, max_ms, p50_ms, sample_count
// Post-processing: none
// Note: both workspace AND session_id filters applied to limit scan scope;
//       remove session_id filter for workspace-wide timing breakdown
// -----------------------------------------------------------------------------
MATCH (d:Delegation {workspace: $workspace, session_id: $session_id})
WHERE d.agent CONTAINS ':'
  AND d.ended_at IS NOT NULL
  AND d.started_at IS NOT NULL
WITH split(d.agent, ':')[0] AS bundle,
     split(d.agent, ':')[1] AS component,
     duration.inMilliseconds(
       datetime(d.started_at), datetime(d.ended_at)
     )                       AS duration_ms
RETURN bundle,
       component,
       round(avg(duration_ms))               AS avg_ms,
       max(duration_ms)                      AS max_ms,
       round(percentileCont(duration_ms, 0.5)) AS p50_ms,
       count(*)                              AS sample_count
ORDER BY avg_ms DESC;

// -----------------------------------------------------------------------------
// QUERY: s02_agent_timing_cross_session
// Signal: S-2 — workspace-wide agent timing (no session filter)
// Tier: A — pure Cypher
// Parameters: $workspace
// Output: bundle, component, avg_ms, max_ms, p50_ms, total_invocations
// -----------------------------------------------------------------------------
MATCH (d:Delegation {workspace: $workspace})
WHERE d.agent CONTAINS ':'
  AND d.ended_at IS NOT NULL
  AND d.started_at IS NOT NULL
WITH split(d.agent, ':')[0] AS bundle,
     split(d.agent, ':')[1] AS component,
     duration.inMilliseconds(
       datetime(d.started_at), datetime(d.ended_at)
     )                       AS duration_ms
RETURN bundle,
       component,
       round(avg(duration_ms))               AS avg_ms,
       max(duration_ms)                      AS max_ms,
       round(percentileCont(duration_ms, 0.5)) AS p50_ms,
       count(*)                              AS total_invocations
ORDER BY avg_ms DESC;

// -----------------------------------------------------------------------------
// QUERY: s07_parallel_fanout
// Signal: S-7 — parallel fan-out detection (concurrent agent spawns)
// Tier: A — parallel_group_id is lifted to top-level on DelegateAgentSpawnedEvent
// Definition: a parallel_group_id shared by >1 spawned events = concurrent fan-out
// Parameters: $session_id
// Output: group_id, agents (list), fan_out, session_id
// Post-processing: Python may split agent strings to extract bundle namespaces
// -----------------------------------------------------------------------------
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

// -----------------------------------------------------------------------------
// QUERY: s07_parallel_fanout_cross_session
// Signal: S-7 — workspace-wide parallel fan-out frequency
// Tier: A — uses DelegateAgentSpawnedEvent via Session anchor
// Parameters: $workspace
// Output: session_id, group_id, agents, fan_out — ordered by fan_out DESC
// -----------------------------------------------------------------------------
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
