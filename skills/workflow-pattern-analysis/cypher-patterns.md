# Workflow Pattern Analysis — Cypher Query Templates

Verified Cypher query templates for cross-run workflow analysis in the CI property graph.

Schema reference: `@context-intelligence:context/graph-model-reference.md`

Companion to: `workflow-pattern-analysis/SKILL.md`

---

## Query Discipline (apply to every query)

- Default `LIMIT 25`. Never omit LIMIT.
- Run `RETURN count(*) AS total` before fetching rows when result set size is unknown.
- Paginate with `SKIP N LIMIT 25` for large result sets.
- Bound all path traversals: use `*1..3` — never unbounded `*`.
- Project specific fields: `RETURN s.session_id, s.status` not `RETURN s`.

**Token cost reference:**
- 1 projected row (3–4 fields) ≈ 15–30 tokens
- 25 projected rows ≈ 500 tokens (safe default)
- 100 full node objects ≈ 5,000–15,000 tokens (dangerous)

---

## Q1 — Enumerate Runs of a Workflow

Find all sessions matching a workflow scope. Adapt the WHERE clause to the
run definition established in Step 1 of the skill (workspace slug, duration,
event count, or session characteristics).

```cypher
// Enumerate root sessions for a workspace-scoped workflow
MATCH (s:RootSession)
WHERE s.workspace CONTAINS $workspace_hint
RETURN
  s.session_id    AS session_id,
  s.status        AS status,
  s.started_at    AS started_at,
  s.last_event_at AS last_event_at
ORDER BY s.started_at DESC
LIMIT 50
```

```cypher
// Count total sessions for pagination planning
MATCH (s:RootSession)
WHERE s.workspace CONTAINS $workspace_hint
RETURN count(s) AS total
```

For sub-session workflows (a specific agent class or recipe step pattern), use
`:ForkedSession` and add filters on `agent_name` or `recipe_step` if those
properties are present in the session metadata.

---

## Q2 — Outcome Classification

Split runs into successful, degraded, and failed groups.

```cypher
// Successful runs: completed with no compaction events
MATCH (s:RootSession)
WHERE s.workspace CONTAINS $workspace_hint
  AND s.status = 'completed'
OPTIONAL MATCH (s)-[:HAS_EVENT]->(e:Event)
  WHERE e.event_type = 'context:compaction'
WITH s, count(e) AS compaction_count
WHERE compaction_count = 0
RETURN
  s.session_id    AS session_id,
  'successful'    AS outcome,
  compaction_count
LIMIT 25
```

```cypher
// Failed runs: cancelled or stale-running
MATCH (s:RootSession)
WHERE s.workspace CONTAINS $workspace_hint
  AND (s.status = 'cancelled'
       OR (s.status = 'running'
           AND s.last_event_at < datetime() - duration('PT2H')))
RETURN
  s.session_id    AS session_id,
  'failed'        AS outcome,
  s.status,
  s.last_event_at AS last_active
LIMIT 25
```

```cypher
// Degraded runs: completed but with at least one compaction event
MATCH (s:RootSession)
WHERE s.workspace CONTAINS $workspace_hint
  AND s.status = 'completed'
MATCH (s)-[:HAS_EVENT]->(e:Event)
  WHERE e.event_type = 'context:compaction'
WITH s, count(e) AS compaction_count
WHERE compaction_count >= 1
RETURN
  s.session_id    AS session_id,
  'degraded'      AS outcome,
  compaction_count
ORDER BY compaction_count DESC
LIMIT 25
```

---

## Q3 — S1 Compaction Signal Across Runs

```cypher
// Compaction count per session, with severity classification
MATCH (s:Session)
WHERE s.workspace CONTAINS $workspace_hint
OPTIONAL MATCH (s)-[:HAS_EVENT]->(e:Event)
  WHERE e.event_type = 'context:compaction'
WITH s, count(e) AS compaction_count
WHERE compaction_count > 0
RETURN
  s.session_id     AS session_id,
  s.status         AS status,
  compaction_count,
  CASE
    WHEN compaction_count >= 10 THEN 'severe'
    WHEN compaction_count >= 3  THEN 'candidate'
    ELSE 'low'
  END AS s1_severity
ORDER BY compaction_count DESC
LIMIT 25
```

---

## Q4 — S3 Iteration Count Across Runs

```cypher
// High-iteration sessions (S3 signal)
MATCH (s:Session)
WHERE s.workspace CONTAINS $workspace_hint
MATCH (s)-[:HAS_EVENT]->(e:Event)
  WHERE e.event_type CONTAINS 'orchestrator:iteration'
WITH s, count(e) AS iteration_count
WHERE iteration_count >= 20
RETURN
  s.session_id    AS session_id,
  s.status        AS status,
  iteration_count,
  CASE
    WHEN iteration_count >= 40 THEN 'severe'
    ELSE 'candidate'
  END AS s3_severity
ORDER BY iteration_count DESC
LIMIT 25
```

---

## Q5 — S9a Delegation Count Across Runs

```cypher
// Delegation count per root session (S9a signal)
MATCH (root:RootSession)
WHERE root.workspace CONTAINS $workspace_hint
OPTIONAL MATCH (root)-[:HAS_TOOL_CALL]->(tc:ToolCall)
  WHERE tc.tool_name = 'delegate'
WITH root, count(tc) AS delegate_calls
RETURN
  root.session_id AS session_id,
  root.status     AS status,
  delegate_calls,
  delegate_calls >= 5 AS s9a_fires
ORDER BY delegate_calls DESC
LIMIT 25
```

```cypher
// Delegation depth: how far does the HAS_FORK chain go?
MATCH path = (root:RootSession)-[:HAS_FORK*1..5]->(leaf:Session)
WHERE root.workspace CONTAINS $workspace_hint
WITH root, max(length(path)) AS max_depth, count(DISTINCT leaf) AS sub_count
RETURN
  root.session_id AS session_id,
  max_depth,
  sub_count,
  root.status
ORDER BY max_depth DESC
LIMIT 25
```

---

## Q6 — Signal Prevalence: Failing vs Successful (Delta Table)

Run Q6a and Q6b, then compute delta = failing_pct - success_pct per signal.
The delta is the primary finding — signals with high delta discriminate failure
from success for this workflow.

```cypher
// Q6a: S1 prevalence in failing runs
MATCH (s:RootSession)
WHERE s.workspace CONTAINS $workspace_hint
  AND s.status IN ['cancelled', 'running']
OPTIONAL MATCH (s)-[:HAS_EVENT]->(e:Event)
  WHERE e.event_type = 'context:compaction'
WITH
  count(DISTINCT s) AS total_failing,
  count(DISTINCT CASE WHEN e IS NOT NULL THEN s END) AS failing_with_s1
RETURN
  total_failing,
  failing_with_s1,
  round(100.0 * failing_with_s1 / total_failing, 1) AS s1_pct_failing
```

```cypher
// Q6b: S1 prevalence in successful runs (baseline)
MATCH (s:RootSession)
WHERE s.workspace CONTAINS $workspace_hint
  AND s.status = 'completed'
OPTIONAL MATCH (s)-[:HAS_EVENT]->(e:Event)
  WHERE e.event_type = 'context:compaction'
WITH s, count(e) AS compaction_count
WITH
  count(DISTINCT s) AS total_success,
  count(DISTINCT CASE WHEN compaction_count = 0 THEN s END) AS clean_success,
  count(DISTINCT CASE WHEN compaction_count > 0 THEN s END) AS success_with_s1
RETURN
  total_success,
  success_with_s1,
  round(100.0 * success_with_s1 / total_success, 1) AS s1_pct_success
```

Repeat the pattern (Q6a + Q6b) for each signal of interest, adapting the
OPTIONAL MATCH condition to the signal's event_type or ToolCall property.

---

## Q7 — Failure Point Localisation

Find the tool calls that appear most frequently in failing sessions and which
tool calls result in errors.

```cypher
// Most common tools in failing sessions
MATCH (s:RootSession)-[:HAS_TOOL_CALL]->(tc:ToolCall)
WHERE s.workspace CONTAINS $workspace_hint
  AND s.status IN ['cancelled', 'running']
RETURN
  tc.tool_name         AS tool_name,
  count(tc)            AS call_count,
  count(DISTINCT s)    AS sessions_using
ORDER BY call_count DESC
LIMIT 15
```

```cypher
// Tools with result failures (result_success = false)
MATCH (s:Session)-[:HAS_TOOL_CALL]->(tc:ToolCall)
WHERE s.workspace CONTAINS $workspace_hint
  AND tc.result_success = false
RETURN
  tc.tool_name       AS tool_name,
  count(tc)          AS failure_count,
  count(DISTINCT s)  AS sessions_affected
ORDER BY failure_count DESC
LIMIT 15
```

```cypher
// Compare tool call distribution: failing vs successful sessions
MATCH (s:RootSession)-[:HAS_TOOL_CALL]->(tc:ToolCall)
WHERE s.workspace CONTAINS $workspace_hint
WITH
  tc.tool_name AS tool_name,
  sum(CASE WHEN s.status IN ['cancelled', 'running'] THEN 1 ELSE 0 END) AS in_failing,
  sum(CASE WHEN s.status = 'completed' THEN 1 ELSE 0 END) AS in_successful
RETURN tool_name, in_failing, in_successful,
       in_failing - in_successful AS delta
ORDER BY delta DESC
LIMIT 15
```

---

## Q8 — Sessions at Risk (Trajectory Detection)

Identify in-progress sessions whose current signal trajectory predicts failure.

```cypher
// Running sessions with early compaction signals (S1 leading indicator)
MATCH (s:RootSession)-[:HAS_EVENT]->(e:Event)
WHERE s.status = 'running'
  AND e.event_type = 'context:compaction'
WITH s, count(e) AS compaction_count
WHERE compaction_count >= 2
RETURN
  s.session_id    AS session_id,
  compaction_count,
  s.last_event_at AS last_active,
  'compaction_risk' AS signal
ORDER BY compaction_count DESC
LIMIT 10
```

```cypher
// Running sessions with high delegation (S9a leading indicator)
MATCH (root:RootSession)-[:HAS_TOOL_CALL]->(tc:ToolCall)
WHERE root.status = 'running'
  AND tc.tool_name = 'delegate'
WITH root, count(tc) AS delegate_calls
WHERE delegate_calls >= 5
RETURN
  root.session_id  AS session_id,
  delegate_calls,
  root.last_event_at AS last_active,
  'delegation_risk' AS signal
ORDER BY delegate_calls DESC
LIMIT 10
```

---

## Composing a Workflow-Specific Query Set

After running the queries above, produce a workflow-specific query set
(written to `{{output_path}}/queries/{{workflow}}-cypher.md`) by:

1. Replacing `$workspace_hint` with the confirmed workspace slug for this workflow
2. Adjusting outcome classification thresholds to match the corpus findings
   (e.g. if the workflow never produces compaction, drop S1 from the delta table)
3. Adding any workflow-specific filters (recipe name, session duration range,
   minimum event count)
4. Testing each query against the live graph server before including it in the set

The final query set should answer at minimum:
- **Q-enum**: All runs with outcome classification
- **Q-signals**: The top 3 discriminating signals (highest delta)
- **Q-risk**: In-progress sessions at risk
- **Q-points**: Failure point localisation (which tools appear before failure)
