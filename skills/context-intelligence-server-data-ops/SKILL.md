---
name: context-intelligence-server-data-ops
version: 1.0.0
description: Exact step order, wording, and the "session details" block format for the three delete flows the server-data-ops agent drives — delete the current session, find-then-delete a session by description, and delete a session someone else created.
license: MIT
---

# Context Intelligence Server Data Ops

Step-by-step procedure for deleting Context Intelligence session data from a server. This
skill exists so the three delete flows are repeatable — the same steps, the same wording,
every time — rather than improvised fresh from the agent body each run.

---

## When to Use

Load this skill whenever the `server-data-ops` agent is about to run any delete flow:
deleting the current session, finding a session by description and deleting it, or
deleting a session someone else created.

## When NOT to Use

- **Just previewing, no delete intended** — a plain `session_summary` call does not need
  this skill's full procedure; only load it when a delete is actually on the table.
- **Reading/analysing session content** — that is `graph-analyst`'s job (see
  `context-intelligence-graph-query`), not this skill.

---

## The tools this skill drives

- **`session_summary`** — read-only preview. Returns `{source: {name, url, origin}, summary: {...}}`.
  The `summary` object's fields (from the server's `DeletionPreview`):

  | Field | Meaning |
  |---|---|
  | `root_id` | The id you looked up (the root of the whole graph that would be removed) |
  | `session_ids` | Every session id in that graph (root + descendants) |
  | `node_count`, `edge_count`, `blob_count` | Totals for the whole graph |
  | `created_by` | Who created the root session |
  | `started_at`, `last_change` | ISO-8601 timestamps |
  | `subsession_count` | How many sessions under the root |
  | `workspace`, `working_dir` | Where it ran |
  | `deletable` | `false` if anything in the graph is still receiving data |
  | `pending_sessions` | Which session ids are still receiving data, if any |

- **`delete_session`** — the real, permanent change. Returns
  `{source: {...}, result: {root_id, session_count, nodes_deleted, relationships_deleted,
  blobs_deleted, queue_sessions_cleaned}}`.

- **`whoami`** — read-only identity lookup. Returns
  `{contributor_id: <github-id-or-null>, source: {name, url, origin}}`. This is how you
  find out who the acting user actually is, for the **same server** a session lives on —
  it never talks to a different server than the one you're checking ownership against.
  Used in Flow 3 to decide whether the ownership warning applies at all (see below).

All three accept `source` (name a specific server) and `list_sources: true` (discover the
connectable set without acting). None takes a workspace input — you always address a
session by its id, and the server resolves the rest.

---

## Resolving "this session" and "the current user"

- **Current session id**: this comes from Amplifier's own runtime context — the
  `Session ID` field in the status context injected into your turn every turn (other
  agents in this ecosystem are shown the same running "Session ID"). For any "this
  session" / "my current session" / "this working directory" request, that field's value
  IS the session to act on. Do not ask the user to type an id for this kind of request.
  If the user types one anyway, it does **not** replace the runtime value — resolve from
  context regardless of what was typed. (Asking the user directly is only ever a fallback
  for the rare case where context genuinely has no `Session ID` at all — at most one
  short question, never an interrogation.) Resolution is not the end of the story: Flow 1
  step 2 requires you to then *prove* the resolution with a real `session_summary` call
  before doing anything else — never act on a resolved id you haven't proven.
- **Current user identity (for ownership comparisons)**: do **not** read this from
  injected context and do **not** guess. Call the `whoami` tool for the **same server**
  the session in question is on, and read its `contributor_id`. That is your one and only
  reference identity for the ownership comparison — in Flow 1 this runs as part of the
  proof step (step 2, below); see Flow 3 for the full match/mismatch/null handling.

---

## The "session details" block

Use this exact shape whenever you present a candidate or a confirmed target to the user
(Flow 2 candidate list; the pre-delete confirmation in any flow):

```
Session <root_id>
  Created by:     <created_by>
  Started:        <started_at>
  Last updated:   <last_change>
  Sub-sessions:   <subsession_count>
  Working dir:    <working_dir>
  Server:         <source.name> (<source.url>)
  Still live:     <"yes — cannot delete yet" if not deletable, else "no">
  Summary:        <narrative from graph-analyst, or "not available">
```

Fill every field from a real `session_summary` call and (for Summary) a real delegation to
`graph-analyst` — never fabricate a value you did not receive.

### Building the narrative (delegate to graph-analyst)

The free-text "what was this session about" part of the session details block is **not**
returned by the server — the server has no intelligence for it. Get it by delegating:

```
Delegate to: graph-analyst
Task: Give me a high-level overview of the work in session <id> — what was done, its
scope and intent — built from that session's own (root) prompts only. Do not dive into
any of its subsessions.
```

Building it from the root session's prompts only keeps the overview fast and focused on
top-level intent, rather than walking the whole subsession tree. Fold the returned
narrative into the "Summary:" line of the details block. If graph-analyst cannot produce
one (server unreachable, no data), write "not available" in that line instead of
inventing one.

**Forbidden shortcut: never put the session's raw first prompt (or any other raw prompt
text) into the "Summary:" line instead of delegating.** A raw quote is not a narrative,
even when it looks descriptive enough to stand in for one — always delegate to
`graph-analyst` first, and fall back to "not available" only if that delegation itself
fails to produce anything.

---

## Flow 1 — delete the current session

**Trigger for this flow — the user's phrasing, not whether a session id is present.**
Flow 1 applies whenever the request refers to the user's own current session — phrases
like "my current session," "my session's data," "this session," "this working directory,"
"the session I'm in." This is true **even if the user also supplies a session id in the
same request** — a supplied id does not downgrade a "my current session" request out of
Flow 1, and it does not replace the runtime session id either (see step 1 below). The
folder-exclusion offer below still applies. Only route to Flow 2 (find by description)
when the request names or searches for some *other* session — by topic, by someone else,
or any session that is not the user's own current one.

Steps, in this exact order:

1. **RESOLVE.** Take the current session id from Amplifier's own runtime context — the
   `Session ID` field in your status context (see "Resolving 'this session' and 'the
   current user'" above). That is the session to act on. Do **not** ask the user for an
   id. If the user also typed one in their request, it does not replace the runtime id —
   you still resolve from Amplifier's context, not from what was typed.
2. **PROVE.** Call `session_summary(session_id=<resolved>, list_sources=true)` and show
   the user the proof — not just "ok, found it," the actual fields:
   - the resolved root session id (`root_id`)
   - `created_by`, confirmed against your own identity — call `whoami` for the same
     server and compare; this is the same ownership check described in Flow 3, running
     here as part of the proof, right after the preview and before anything else
   - `working_dir`
   - `last_change` — flag it explicitly if it is under a minute old ("this may still be
     live")
   Also note, from the `list_sources: true` result, every server this session actually
   exists on (feeds the all-servers completeness handling in step 6).
   **If `session_summary` 404s on every configured server, STOP.** Tell the user plainly
   — e.g. "this session isn't on the server(s)" — and go no further. Never delete against
   an unresolved or absent session.
3. **OFFER THE FOLDER EXCLUSION — mandatory, unconditional, front-loaded.** Before the
   impact statement, before asking for confirmation, before any delete: offer to add a
   folder exclusion for the `working_dir` you just proved in step 2 (see "Folder
   exclusion" below), and offer to guide the user through applying it. Make this offer
   every time, whether or not you have any way to confirm the folder is currently
   filter-included; do not skip or gate the offer on that check. **Skipping this offer in
   Flow 1 is a defect, not a shortcut.**
4. **STATE THE IMPACT.** Tell the user plainly what will be removed: the session's whole
   graph — the named session plus every descendant (forks, sub-sessions, delegated
   children) — along with the blobs and queue records for all of them, permanently, and
   name which server(s) this applies to (from step 2's server check). Nodes shared with
   other sessions are kept; there is no undo and no restore.
5. **CONFIRM.** Get an explicit, strong confirmation from the user, restating the
   resolved session id and the server(s) about to be affected — a vague "yes" is never
   enough.
6. **DELETE, then VERIFY, on every server (all-servers completeness).** If step 2 found
   the session on more than one server, name all of them to the user and ask which to
   delete from (or "all") if that was not already settled by step 5's confirmation. For
   **each** server chosen (one at a time, not just the first): call `session_summary`
   again as an immediate pre-delete preview if meaningful time has passed since step 2,
   then call `delete_session`, then verify that server's own result (or a fresh
   `session_summary`) before moving to the next one — one delete succeeding says nothing
   about whether the others did. Report exactly what was removed and from which
   server(s) — and if the session still exists on any server that was not chosen for
   deletion, say so explicitly (by name). Never say "done" or imply full removal while a
   server you didn't act on (or didn't check) still holds the session.

All six steps happen in this same session.

### Folder exclusion (offered in step 3, before anything else proceeds)

The fan-out filter for a destination lives at
`overrides.hook-context-intelligence.config.destinations.<name>.exclude` in
`~/.amplifier/settings.yaml` — a list of gitignore-style path patterns matched against a
session's working directory. Adding a pattern that matches the current working directory to
that destination's `exclude` list stops that destination from being selected for future
sessions started in that folder.

**How you apply it:** the agent has no filesystem tool, and that's deliberate — it never
edits this file itself. **In Flow 1, make this offer every time, unconditionally, and
before the impact statement, confirmation, or delete (steps 4–6)** — do not first try to
determine whether the current session's folder is actually included by a chosen
destination's filters. That determination is not reliably available to the agent, and
gating the offer on it (or deferring it later in the flow) is exactly what caused the
offer to be silently skipped in a real case (a current-session deletion where the offer
never fired). Instead: always show the user exactly what to add (the destination name,
and the pattern that would match their current folder), and offer to guide them through
applying it, before proceeding to the impact statement, confirmation, or deletion.
Confirm whether they applied it, then move on.

Order: resolve (step 1) → prove (step 2) → offer the exclusion (step 3, always) → state
the impact (step 4) → strong confirmation (step 5) → delete and verify (step 6).

QUESTION FOR USER: it is unclear whether an exclusion added while the current session is
still running takes effect for that session's own remaining event pushes to this
destination, or only for sessions started after it. The approved scenario does not
address this. Please confirm whether this is acceptable as-is or needs a different
resolution before this flow ships.

## Flow 2 — find a session by description, then delete

1. Take the user's description (topic, date range, sometimes a named server/workspace).
2. Narrow candidates with `graph_query`. Reliable scoping fields on `Session` nodes:
   `workspace`, `created_by`, `started_at`/`last_updated` (wrap date literals in
   `datetime()`). Do **not** filter on a raw graph `working_dir` property — it is not
   reliably populated in the graph (see the `context-intelligence-graph-query` skill); the
   working directory you show the user comes from `session_summary`, not from Cypher.
   Cap the candidate set to a small number (a handful) before doing per-candidate work.
3. For each shortlisted candidate: call `session_summary(session_id=<candidate>)` for the
   accurate facts.
4. **Non-skippable gate — before presenting any details block:** for each shortlisted
   candidate, delegate to `graph-analyst` for the root-prompt narrative overview (see
   "Building the narrative" above). You MUST NOT present a session details block for a
   candidate until you have either the `graph-analyst` narrative or an explicit "narrative
   not available" from a failed delegation for that candidate. Writing your own summary
   from memory, or quoting the prompt, is forbidden — this step fired once and was
   silently skipped once on identical requests in eval, so treat it as required every
   time, never optional.
5. Build a session details block for each candidate, using the facts from step 3 and the
   narrative (or "not available") from step 4.
6. Present the candidates (their details blocks) to the user and let them pick one.
7. All-servers completeness check (same rule as Flow 1): call `session_summary` or
   `delete_session` with `list_sources: true` for the chosen id and check it against
   **every** server in the connectable set. If it exists on more than one, name all of
   them to the user and ask which to delete from (or "all") before continuing.
8. For **each** server chosen: re-run `session_summary` on the chosen id right before
   delete — a fresh preview, not the one from the candidate list, in case anything changed
   in between.
9. Get an explicit, strong confirmation: restate exactly what will be permanently removed
   (session id, counts) and from which server, and require a clear go-ahead — a plain "yes"
   with no restatement is not enough.
10. Call `delete_session` on the chosen id and server, and verify that server's own result
    before moving to the next chosen server.
11. Report exactly what was removed and from which server(s) — and if the session still
    exists on any server that was not chosen, say so explicitly by name. Never say "done"
    or imply full removal while an unchecked or unchosen server still holds the session.

## Flow 3 — deciding whether an ownership warning applies

This flow is not a separate user-facing path — it's the ownership check that runs inside
Flow 1 or Flow 2, right after the preview and before asking for the delete confirmation.
Its whole job is to decide, correctly, whether to show the "not created by you" warning —
and, just as importantly, to **not** show it when the session genuinely belongs to the
current user.

1. Run Flow 1 or Flow 2 up through the preview step (`session_summary`), but do **not**
   ask for the delete confirmation yet. Note the previewed session's `created_by`.
2. Call `whoami` for the **same server** the session is on (pass the same `source` you
   used for the preview). Read its `contributor_id`.
3. Compare `contributor_id` to the session's `created_by`:
   - **They match** → this is the user's own session. Do **not** show any ownership
     warning. Continue as Flow 1/2 normally (single confirmation, no extra step).
   - **They differ** → genuine not-owned case:
     - State plainly: "this session was created by `<created_by>`, not you."
     - Ask a **separate, explicit, strong** confirmation — restating what will be
       permanently removed and from which server — that the user still wants to delete
       someone else's data, before proceeding.
     - Only call `delete_session` after that second, explicit confirmation.
   - **`contributor_id` is null** (auth disabled server-side, or otherwise unresolvable)
     → you cannot confirm ownership either way. Say so plainly ("I can't confirm who
     created this session on this server") and ask the user directly whether it's theirs,
     rather than defaulting to the warning. Do not treat a null `contributor_id` as
     evidence of a mismatch, and do not skip asking.

**Never skip step 2.** Warning based on `created_by` alone, without first resolving the
acting user via `whoami`, is exactly the mistake that produced false "not created by you"
warnings on the user's own sessions in a real evaluation.

---

## Multi-server handling (all flows)

- `list_sources: true` on any of the three tools returns the connectable set: every server
  this agent can reach, each with `name`, `url`, `origin` (`source` or `destination`).
- Passing `source=<name>` addresses one specific server by name from that set.
- Omitting `source` uses a default: the single configured tool source if there is exactly
  one, otherwise the first configured destination. If two or more tool **sources** are
  configured and none is named, the tool refuses and lists the valid names — pass one.
- Always state, in your reply to the user, which server (`source.name`) answered or was
  acted on.

## Errors to expect and how to talk about them

- **404** — the session id is not known to that server. Say so plainly; check for a typo
  or ask whether it might be on a different server.
- **409** — the session is still receiving data (cannot be deleted yet), or its id is
  ambiguous across workspaces. Never force it or retry aggressively — tell the user it is
  still live.
- **`ambiguous_source_selection` / `unknown_source`** — a source-selection problem, not a
  server error. Call with `list_sources: true` and ask the user to pick a valid name.

---

## Design notes

- **Current session id** — resolved from Amplifier's own runtime context (the `Session
  ID` field) first and always for a "this session" request; a typed id never replaces
  it. Asking the user is the fallback only when context genuinely has no `Session ID` at
  all. See "Resolving 'this session' and 'the current user'" above and Flow 1 step 1 —
  and step 2, which proves the resolution before anything else proceeds.
- **Current user identity for ownership** — resolved via the `whoami` tool, never from
  injected context and never guessed. See "Resolving 'this session' and 'the current
  user'" above and Flow 3. In Flow 1 this runs as part of step 2 (the proof step).
- **The folder-exclusion mechanism** — the agent has no filesystem tool, deliberately. It
  shows the user the exact setting to add and asks them to apply it; it never edits
  `~/.amplifier/settings.yaml` itself (see "Folder exclusion" under Flow 1 above). The
  offer itself is mandatory, unconditional, and front-loaded — step 3 of Flow 1, before
  the impact statement, confirmation, or delete — never gated on first confirming the
  folder is filter-included, and never deferred to later in the flow.
- **Folder-exclusion timing** — still an open question; see the QUESTION FOR USER note
  under "Folder exclusion" above. Needs a decision before this flow ships.
