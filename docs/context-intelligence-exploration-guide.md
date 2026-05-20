# Context Intelligence: What to Explore and How

> I built this after the Data Layer 2 rollout and the `SOURCED_FROM` bridge.
> It's the list of things I've been testing myself, organized for jumping in and poke at the same stuff. Not a test plan — more like "here's
> what I found interesting and what's worth trying."

---

## 1. Getting Up to Date

Before anything — make sure you're on the latest server and bundle. Both changed a lot recently.

### Verify the Connection

Start a session with the context-intelligence bundle and ask:

> *"Are you connected to the context intelligence server? What version is it?"*

You should see `v2.0.0`. If it says unreachable or shows an old version, something needs updating.

---

## 2. What's New — The Short Version

### Data Layer 2 (Amplifier Core domain concepts in space-time semantic)

OK so this is the big one. The server now builds a **two-layer graph**. Data Layer 1 — raw events — (always existed) captures every raw event as nodes — galaxies of events, basically. Data Layer 2 — Amplifier Core domain concepts in space-time semantic — (new) assembles those raw events into meaningful semantic entities:

| Entity | What it represents |
|---|---|
| `Session` | A complete Amplifier session (root or agent sub-session) |
| `ToolCall` | One invocation of a tool — with input, output, success/failure |
| `OrchestratorRun` | The entire orchestrator loop for a session |
| `Iteration` | A single turn of the orchestrator loop (LLM call + tool calls) |
| `ContentBlock` | An individual response block from the LLM |
| `Prompt` | A user message submitted to the session |
| `Turn` | A conversation exchange, chained across the session |
| `MountPlan` | The bundle/agent configuration that was active |
| `Cancellation` | A session that was explicitly cancelled |
| `ContextCompaction` | A compaction event with before/after context sizes |

**What this actually means:** Instead of digging through raw events asking "what happened in session X", you can now ask things like "which agent sessions had the most tool failures" or "show me the delegation tree for this run" and get structured, navigable answers. That's the density of Data Layer 2 — same data, way more useful questions.

### `SOURCED_FROM` Cross-Layer Bridge

Every Data Layer 2 semantic entity now has a `SOURCED_FROM` edge pointing back to the exact raw Data Layer 1 event that produced it. This is the provenance link — you can always trace "where did this come from?" all the way down to the original event payload.

**Why this matters:** When the agent finds something weird in the semantic graph, it can drill into the raw payload to see exactly what the session emitted. No more guessing.

### Tool Errors are First-Class

Previously, tool errors (`tool:error` events) were captured as raw events but didn't close out the semantic `ToolCall` node — left it dangling. Now they're properly handled: a `ToolCall` that errored will have `result_success=False` and `error_type` populated. You can actually query for and analyze tool failures now!

### Rewritten Graph-Query Skill

I completely rewrote the skill that teaches `graph-analyst` how to query the graph — it now reflects the Data Layer 2 schema, SST edge types, and proper multi-session navigation patterns. The agent should produce significantly better Cypher queries and navigate delegation trees more reliably.

---

## 3. Scenarios to Try

### Scenario A — Basic Session Recap

**The idea:** Let's see if the core "what happened?" use case works end-to-end.

After running any Amplifier session (even a short one), open a new session with the context-intelligence bundle and ask:

> *"Give me a recap of my most recent session — what did it do, what tools did it call, and how many turns did it have?"*

**What to look for:**
- The agent identifies the correct most-recent session
- It reports tool names, not just event types
- The turn count is reasonable (not 1, not inflated by internal events)
- The summary reads as a coherent narrative of what happened

**If something looks off:** If the agent returns a wall of raw event data, or reports tool names like `tool:pre` instead of actual tool names like `bash` or `write_file`, or if turn counts seem weird — that's the agent falling back to Data Layer 1 (raw events) instead of using Data Layer 2 entities.

---

### Scenario B — Delegation Tree Exploration

**The idea:** This is the scenario that benefits most from Data Layer 2. Multi-agent session navigation — actually seeing the tree.

Run any session that spawns sub-agents (a recipe, or any `delegate()` call), then ask:

> *"Show me the delegation tree for my last recipe run — what agents were spawned and what did each one do?"*

Or more specifically:

> *"In my last session, how many sub-agent sessions were spawned? Which ones used the most tool calls?"*

**What to look for:**
- Parent-child session relationships are correctly traversed
- Sub-sessions are identified by agent name, not just session ID
- Tool call counts per agent are accurate
- The tree structure makes sense given what you asked for

**Bonus — try this one:**
> *"Which of my recent sessions spawned the deepest delegation tree?"*

This query surfaces sessions with complex agent orchestration that you might not have realized were that deep. It's particularly relevant for understanding how recipes actually fan out.

---

### Scenario C — Tool Failure Audit

**The idea:** Exercise the new first-class tool error capture. This is where `result_success=False` pays off.

This is most useful if you've had sessions that hit errors. Ask:

> *"Across my recent sessions, which tools have failed? Show me the error types."*

Or:

> *"Were there any tool failures in my last 5 sessions? What was the context around them?"*

**What to look for:**
- The agent returns `ToolCall` nodes with `result_success=False`
- Error types are populated (not just "error")
- The surrounding context (which session, which iteration) is present
- If there are no failures, the agent clearly says so (not just returns empty)

**If something looks off:** If the agent returns nothing for sessions where you know tools failed, the `tool:error` handling may not be working end-to-end. Also interesting: if you see error types that are surprising or frequent — that's real signal about friction points in the tooling.

---

### Scenario D — Comparing Two Sessions

**The idea:** Cross-session comparative queries — this is the first time you can actually ask "which of these two approaches cost more?" without reconstructing events across files.

Run two similar sessions (e.g., the same kind of task done slightly differently), then ask:

> *"Compare my last two sessions — which one called more tools, had more turns, and used the most context?"*

Or for recipe work:

> *"I ran the same recipe twice. Which run was faster? Which had more tool calls?"*

**What to look for:**
- The agent correctly identifies and separates the two sessions
- The comparison is on semantic dimensions (turns, tool calls) not raw event counts
- The numbers are plausible given what you did

**If something looks off:** If the agent can't distinguish "last two" from "last one" or conflates sub-sessions with the comparison, that's a navigation issue worth noting.

---

### Scenario E — Large Payload Exploration with Blob Reading

**The idea:** Test the blob-reading skill's ability to safely peek at large payloads without dumping everything.

Ask the agent to drill into a specific tool call:

> *"Show me the actual input and output of the longest tool call in my last session."*

Or:

> *"What was the full prompt that was sent to the LLM in the first turn of my last session?"*

**What to look for:**
- The agent uses `blob_read` → temp file → `jq` extraction (not raw blob dump)
- Only the relevant field is returned, not the entire 100k-token payload
- The extracted content is coherent and matches what you'd expect

**If something looks off:** If the agent returns a wall of JSON, or says it can't read the blob, or the extracted field is garbled — that's a blob-reading skill issue. David — the `jq` extraction path is the one to watch here. Also interesting: how long are your prompt payloads? The extracted size tells you something about context efficiency.

---

### Scenario F — Orchestrator Run Depth

**The idea:** Understand iteration patterns — how deeply does the orchestrator loop?

> *"In my recent sessions, what's the typical number of iterations per session? Are there any sessions with unusually high iteration counts?"*

Or:

> *"Which of my recent sessions required the most back-and-forth with the LLM?"*

**What to look for:**
- Iteration counts per `OrchestratorRun` are populated
- The distribution makes sense (most sessions 2–10 iterations, some outliers)
- Sessions with high iteration counts are identifiable by session ID

**What I found interesting here:** High iteration counts often mean the agent got stuck, was correcting mistakes repeatedly, or was doing genuinely complex multi-step work. Seeing which sessions are which is useful for tuning — and it's one of those things where the graph just tells you something you wouldn't notice from the session itself.

---

### Scenario H — Large File Edit Causation

**The idea:** This is the `ContentBlock → ToolCall` causation chain combined with blob reading to explain *why* a big edit happened. David — this one's directly relevant to the DTU/Resolver integration work.

Pick any session where you wrote or heavily edited files. Ask:

> *"Find the largest file edit in my recent sessions — the biggest `write_file` or `apply_patch` call — and trace back through the graph to tell me what the LLM was reasoning about when it decided to make that change."*

**What to look for:**
- The agent finds a `ToolCall` with `tool_name` in (`write_file`, `apply_patch`, `edit_file`) and identifies it as notably large (by input size or content length)
- It traverses `ContentBlock -[LEADS_TO+ caused]-> ToolCall` backward to the thinking or text block that drove the decision
- It uses `SOURCED_FROM` → `blob_read` → `jq` to extract the actual content block text
- The final answer is a human-readable explanation: *what was the agent trying to do when it made that large edit?*

**Why this one matters:** This is the tool causation chain in practice. It's not just "which tools were called" — it's "why did the model decide to call this tool at this point." For large edits especially, there's usually a deliberate reasoning step (a refactor decision, a spec change, a correction) that should be visible in the content blocks. If the chain is broken or the blob reads return nothing useful, that's a `LEADS_TO` edge completeness issue.

---

### Scenario I — Parallel Tool Execution Groups

**The idea:** Validate the `NEAR / parallel_execution` edge semantic by surfacing real parallel dispatch events.

> *"In my recent sessions, how many times were tools dispatched in parallel? For each parallel group, tell me which tools ran together."*

**What to look for:**
- The agent queries `ToolCall -[NEAR parallel_execution]-> ToolCall` edges
- Results are grouped by `parallel_group_id`
- Each group lists the `tool_name` values that fired together and the session they belong to
- If no parallel groups are found across many sessions, the agent says so clearly (not just returns empty — zero parallel usage is itself a valid and interesting data point)

**Why this is interesting:** Parallel tool dispatch is one of the clearest signals of efficient agent orchestration. Sessions using `delegate()` with multiple concurrent agents, or Amplifier recipes with parallel steps, should show parallel `ToolCall` groups. Sessions that are purely sequential won't. Comparing the two tells you which task types actually exploit parallelism and which don't — and whether the payoff in tool count justifies the coordination overhead. David — this is super relevant for understanding how the recipe executor fans out.

**If something looks off:** If you know a session used parallel delegation (e.g., a recipe with concurrent steps) but the agent reports zero parallel groups, the `NEAR` edge is either not being written at ingest time or not being traversed correctly.

---

### Scenario J — Token Pressure Outliers: High Input, Low Output

**The idea:** Find orchestrator runs where a large context load produced very little — high input tokens, low output tokens — and understand what those sessions were doing.

> *"Across my recent sessions, find orchestrator runs where the input token count was disproportionately high compared to the output — where a lot of context was loaded but very little was produced. What were those sessions doing?"*

**What to look for:**
- The agent queries `OrchestratorRun → Iteration` and reads `Iteration.usage.input` and `Iteration.usage.output` for each run
- It identifies runs with a high input:output ratio (e.g., 20:1 or higher)
- It reports the session ID, the `OrchestratorRun.prompt` (what was asked), and the ratio

**Why this one's super interesting:** A high input:output ratio is a cost-efficiency signal. You're paying for a large context window but the model is producing a short response — which might mean the session was near-complete and just needed a small finishing step, or the model got stuck in a loop making small tool calls against heavy context, or the task ended in a cancellation after expensive iterations. These are qualitatively different failure modes. This query directly surfaces the "are we burning tokens efficiently?" question. It exercises `Iteration.usage.*` properties and cross-session aggregation — two Data Layer 2 capabilities that have no straightforward Data Layer 1 (raw events) equivalent.

---

## 4. Skill Schema Verification

The `graph-query` skill that teaches `graph-analyst` how to query the Data Layer 2 graph is loaded from the server at session start. If the server is serving a stale version — or if the agent is using cached schema knowledge rather than the current loaded skill — these probes will catch it. Run them in a **fresh session** to avoid warm-cache effects.

### Probe 1 — Iteration Node Properties

> *"What properties does an `Iteration` node have in the context-intelligence graph?"*

**Expected answer:** The agent should accurately name the confirmed fields: `iteration` (number), `provider`, `model`, `message_count`, `thinking_enabled`, and the usage sub-fields: `usage.input`, `usage.output`, `usage.cache_write`.

**If it fails:** Vague answer ("iterations have various properties"), wrong field names, or fields that belong to a different node type (e.g., citing `turn_count` as an `Iteration` property — that lives on `OrchestratorRun`).

---

### Probe 2 — Parallel Execution Edge

> *"How does the graph represent parallel tool calls? Show me a sample Cypher query that finds sessions which used parallel dispatch."*

**Expected answer:** The agent should reference the `NEAR` relationship type with semantic label `parallel_execution` and the `parallel_group_id` property. The Cypher query should traverse `ToolCall -[NEAR {semantic: 'parallel_execution'}]-> ToolCall` or equivalent, grouped by `parallel_group_id`.

**If it fails:** The agent describes parallelism as a node property rather than an edge, or writes a query that scans `tool:pre` events for a `parallel_group_id` field (Data Layer 1 raw events approach) instead of using the `NEAR` edge (Data Layer 2 approach). That's the agent falling back to raw event thinking.

---

### Probe 3 — ContentBlock Causation Chain

> *"How do I query which LLM decision caused a specific tool call? What's the graph path from a ContentBlock to a ToolCall?"*

**Expected answer:** The agent should explain `ContentBlock -[LEADS_TO+ {semantic: 'caused'}]-> ToolCall`, noting that the join key is `ContentBlock.block_id` matching `ToolCall.tool_call_id`. It should also mention that `SOURCED_FROM` can reach the raw payload if the content text is needed.

**If it fails:** The agent describes a timestamp-based correlation (Data Layer 1 raw events approach), or says the relationship doesn't exist in the graph, or gets the edge direction wrong.

---

**If all three probes pass:** The skill loaded from server reflects the current Data Layer 2 schema and the agent is querying correctly against it. That would be super awesome.

**If any probe fails:** Note which one — the specific probe that failed points directly at which section of the graph-query skill document needs updating.

---

## 5. Things to Watch For (Improvement Signals)

As you explore, note these. I've been collecting them as I test:

| Signal | What it probably means |
|---|---|
| Agent returns raw event types instead of semantic names | Data Layer 2 entity not being used, agent falling back to Data Layer 1 (raw events) |
| Turn counts seem inflated | Turn chaining (E14/E15) not working correctly |
| Agent says "no sessions found" when there clearly are some | Workspace scoping issue |
| Delegation tree looks flat when it shouldn't | Parent-child session linking broken |
| Tool call counts don't match your mental model | `tool:error` not being counted, or pre/post double-counting |
| Blob reads return too much data | `blob-reading` skill not being applied |
| Queries time out on large session histories | Neo4j index gaps |
| Agent confuses root sessions with sub-sessions | Session sub-type labeling issue |
| "What happened?" query is missing a tool you clearly ran | Event not reaching the server (hook issue) |
| Zero parallel groups across many delegation-heavy sessions | `NEAR` edges not being written at ingest, or not traversed |
| Probe 1–3 in §4 return wrong answers | Skill loaded from server is stale or the skill text itself needs updating |

---

## 6. Quick Reference — Prompts to Try

Copy these directly into a context-intelligence session:

```
"What happened in my last session?"

"Show me the delegation tree for my last recipe run."

"Which tools have I called most often this week?"

"Were there any tool failures in my recent sessions?"

"What's the deepest delegation tree I've run recently?"

"Show me the actual output of the bash tool call in my last session."

"Which of my recent sessions had the most iterations?"

"Compare my last two sessions — which was more complex?"

"Find the largest file edit in my recent sessions and tell me what reasoning led to it."

"How many times were tools dispatched in parallel across my recent sessions? What tools ran together?"

"Find sessions where input tokens were disproportionately high relative to output — what were those sessions doing?"

"What properties does an Iteration node have?" [skill schema check]

"Show me a Cypher query for finding parallel tool execution groups." [skill schema check]
```

---

*Last updated: 2026-04-07 · Server: v2.0.0 · Bundle: context-intelligence@main*
