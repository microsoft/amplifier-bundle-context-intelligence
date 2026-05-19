// Signal: S-1 — cross-session frequency ranking across a whole workspace
// Tier: A — uses Delegation SST node (workspace-scoped, no Session traversal needed)
// Performance: Delegation.workspace is a property on a label-scanned node type;
//              acceptable for workspace-scoped cross-session aggregation
// Parameters: $workspace
// Output: bundle, component, total_invocations, session_count, success_pct
// Post-processing: none
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
