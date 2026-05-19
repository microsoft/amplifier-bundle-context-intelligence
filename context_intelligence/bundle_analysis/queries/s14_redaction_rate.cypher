// Signal: S-14 diagnostic — what fraction of sessions have unredacted
//         session:start events?
// Purpose: assess S-14 signal coverage before relying on it for reporting
// Parameters: $workspace
// Output: total_sessions, unredacted, unredacted_pct
// No post-processing needed — purely diagnostic
MATCH (s:Session {workspace: $workspace})
      -[:SOURCED_FROM]->(e:SessionStartEvent)
RETURN count(*) AS total_sessions,
       sum(CASE WHEN e.data CONTAINS '"bundle_name"' THEN 1 ELSE 0 END)
         AS unredacted,
       round(
         100.0
         * sum(CASE WHEN e.data CONTAINS '"bundle_name"' THEN 1 ELSE 0 END)
         / count(*),
         1
       ) AS unredacted_pct;
