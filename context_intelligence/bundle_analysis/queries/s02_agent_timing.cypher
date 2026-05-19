// Signal: S-2 — agent duration per bundle/component (Delegation SST node carries
//               started_at / ended_at as lifted top-level datetime strings)
// Tier: A — pure Cypher, timing fields are lifted to Delegation node
// Parameters: $workspace, $session_id
// Output: bundle, component, avg_ms, max_ms, p50_ms, sample_count
// Post-processing: none
// Note: both workspace AND session_id filters applied to limit scan scope;
//       remove session_id filter for workspace-wide timing breakdown
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
