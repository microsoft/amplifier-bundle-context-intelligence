---
name: context-intelligence-graph-query
version: 2.0.0
description: >
  Use when querying the context-intelligence property graph for session history,
  tool call traces, LLM iteration analysis, execution scale metrics, agent
  delegation trees, skill loading, and recipe orchestration. Covers all graph
  layers, cross-layer SOURCED_FROM joins, SST navigation, blob handling, and
  verified Cypher patterns.
license: MIT
---

# Context Intelligence Graph Query — Server Unavailable

The context intelligence server is not reachable.
Graph-based session analysis is not available in this session.

Delegate immediately to `session-navigator` for all session analysis,
event lookup, and delegation tree tracing. Do not attempt Cypher queries.

---

## Query Result Size Rules (apply whenever the server is available)

These rules apply even when writing queries in the design mode or for later use.

**Always bound every query:**
- Use `LIMIT 25` as the default. Never omit LIMIT.
- Run `RETURN count(*) AS total` before fetching rows if the result set size is unknown.
- Paginate with `SKIP N LIMIT 25` for large result sets.
- Bound all path traversals: use `*1..3` — never `*` (unbounded).
- Project specific fields: `RETURN tc.tool_name, tc.result_success` not `RETURN tc`.

**Token cost reference:**
- 1 projected row (3–4 fields) ≈ 15–30 tokens
- 25 projected rows ≈ 500 tokens (safe)
- 100 full node objects ≈ 5,000–15,000 tokens (dangerous)
- Unbounded traversal on a large session → context overflow
