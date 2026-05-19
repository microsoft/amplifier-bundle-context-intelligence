// Signal: S-2 — workspace-wide agent timing (no session filter)
// Tier: A — pure Cypher
// Parameters: $workspace
// Output: bundle, component, avg_ms, max_ms, p50_ms, total_invocations
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
