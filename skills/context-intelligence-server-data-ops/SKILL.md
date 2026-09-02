---
name: context-intelligence-server-data-ops
version: 1.0.0
description: Exact step order, wording, and the "session details" block format for the delete flows the server-data-ops agent drives — delete the current session, clean up every session from the current working directory, find-then-delete a session by description, and delete a session someone else created.
license: MIT
---

# Context Intelligence Server Data Ops

Step-by-step procedure for deleting Context Intelligence session data from a server, so
the delete flows run the same way every time instead of being improvised fresh.

**This is a direct, interactive conversation with the user** — every decision-critical
step (preview, offer, impact, confirmation) is shown to them directly, in your own
message, never summarized away by delegation.

## When to Use

Load whenever the `server-data-ops` agent is about to run a delete flow: the current
session, every session from the current working directory, a session found by
description, or a session created by someone else.

## When NOT to Use

- A plain `session_summary` preview with no delete on the table doesn't need this.
- Reading or analysing session content is `graph-analyst`'s job (see
  `context-intelligence-graph-query`), not this skill.

---

## Tools

- **`session_summary`** — read-only preview. Returns `{source: {name, url, origin},
  summary: {...}}`. `summary` fields (the server's `DeletionPreview`):

  | Field | Meaning |
  |---|---|
  | `root_id` | The graph's root session id |
  | `session_ids` | Every session id in that graph (root + descendants) |
  | `node_count`, `edge_count`, `blob_count` | Totals for the whole graph |
  | `created_by` | Who created the root session |
  | `started_at`, `last_change` | ISO-8601 timestamps |
  | `subsession_count` | Sessions under the root |
  | `workspace`, `working_dir` | Where it ran |
  | `deletable` | `false` if anything in the graph is still receiving data |
  | `pending_sessions` | Session ids still receiving data, if any |

- **`delete_session`** — the real, permanent delete. Returns `{source: {...}, result:
  {root_id, session_count, nodes_deleted, relationships_deleted, blobs_deleted,
  queue_sessions_cleaned}}`.
- **`whoami`** — read-only identity lookup. Returns `{contributor_id, source: {name,
  url, origin}}` for the server you call it against. Used to compare against a
  session's `created_by` (Flow 3).

All three accept `source` (name a server) and `list_sources: true` (discover the
connectable set without acting). None takes a workspace — you always address a
session by id.

- **`todo`** — the standard todo-list tool. Used only in Flow 1-folder, one item per
  candidate root session found, so a bulk cleanup never silently skips one.

---

## Key Concepts

**Current session id.** Comes from Amplifier's own runtime context — the `Session
ID` field injected into your status context every turn. For any "this session" / "my
current session" request, that value IS the session to act on. Don't ask the user
for an id, and a typed id never replaces it — resolve from context regardless. Only
ask directly if context genuinely has no `Session ID`.

**Current working directory (for Flow 1-folder).** Comes from the same runtime
context — the `Working directory` field injected into your status context every
turn, resolved the same way as the session id above. For any "this folder" / "this
working directory" / "uploaded from here" request, that value is the directory to
search — not a single session id. Don't ask unless context genuinely has none.

**Current user identity (for ownership).** Never read from context and never guess.
Call `whoami` for the *same server* the session in question is on, and use its
`contributor_id` as the one reference identity for the comparison (Flow 3).

## The "Session Details" Block

Use this exact shape for any candidate or confirmed target (Flow 2 candidate list;
the pre-delete confirmation in any flow):

```
Session <root_id>
  Created by:     <created_by>
  Started:        <started_at>
  Last updated:   <last_change>
  Sub-sessions:   <subsession_count>
  Working dir:    <working_dir>
  Server:         <source.name> (<source.url>)
  Still live:     <"yes — cannot delete yet" if not deletable, else "no">
  Summary:        <your synthesized overview of the session, or "not available">
```

Fill every field from a real `session_summary` call. Never fabricate a value you
didn't receive.

**Summary line.** Not returned by the server — build it yourself. Call `graph_query`
to read that session's **root**-session prompts only (never subsessions), then write a
short synthesized overview, in your own words, of what the session was about — its
scope and intent. This must be a synthesis, never a raw or verbatim prompt quote, and
never a from-memory guess. If `graph_query` returns nothing usable for the root
prompts, write "not available."

---

## Flow 1 — Delete the Current Session

Applies whenever the request refers to the user's own current session ("my current
session," "this session," "the session I'm in") — even if the user also supplies a
session id; a supplied id doesn't downgrade it out of Flow 1 or replace the runtime
id. Route to **Flow 1-folder** instead when the request is about the whole working
directory, not just the current session ("this folder," "this working directory,"
"uploaded from here"). Route to Flow 2 when the request names or searches for some
*other* session.

1. **Resolve.** Take the current session id from runtime context (see Key Concepts).
   Don't ask the user for an id.
2. **Prove.** Call `session_summary(session_id=<resolved>, list_sources=true)` and show
   the user: `root_id`, `created_by` (confirmed against `whoami` for the same server),
   `working_dir`, and `last_change` (flag if under a minute old — "may still be live").
   Note every server the session exists on, from `list_sources`. If it 404s on every
   server, stop and tell the user plainly — never delete against an unresolved session.
3. **Offer the folder exclusion to the user directly, and wait for their answer**,
   before anything else proceeds. Show the setting
   `overrides.hook-context-intelligence.config.destinations.<name>.exclude` in
   `~/.amplifier/settings.yaml` — a list of gitignore-style patterns matched against a
   session's `working_dir`; adding one for the current folder stops that destination
   being selected for future sessions there. The agent has no filesystem tool, so it
   shows the setting in its own message and offers to guide the user through applying
   it — never edits the file itself. Make this offer every time, regardless of whether
   you can confirm the folder is currently included. Wait for their answer, confirm
   whether they applied it, then move on.
4. **State the impact.** The whole graph — the session plus every descendant (forks,
   sub-sessions, delegated children) — plus its blobs and queue records, permanently,
   on the server(s) found in step 2. Shared nodes are kept; there is no undo.
5. **Confirm.** Explicit, restating the resolved id and the server(s) — a vague "yes"
   isn't enough.
6. **Delete, then verify, on every server** (see Multi-Server Handling).

---

## Flow 1-folder — Clean Up Everything Pushed From This Working Directory

Applies when the user is worried that data from sessions run in THIS folder was
pushed and should not have been — "I think I uploaded data from sessions in this
folder, I want to delete it," "things from this working directory should never have
been pushed." Still the user's own data (S1), but scoped to every root session that
ran here, not just the current one. Route here instead of Flow 1 when the request is
about the *folder*, not a single session.

1. **Resolve the working directory.** Take it from runtime context (see Key
   Concepts), the same way Flow 1 resolves the current session id. Don't ask the
   user for a path.
2. **Offer the folder exclusion FIRST, and wait for their answer**, before finding
   or deleting anything — same setting as Flow 1 step 3:
   `overrides.hook-context-intelligence.config.destinations.<name>.exclude` in
   `~/.amplifier/settings.yaml`. Show it in your own message and offer to guide the
   user through applying it — you never edit the file yourself. Confirm it's applied
   before moving on: while the folder is still in scope, continued ingestion would
   keep re-creating the very data you are about to delete.
3. **Find.** Delegate to `graph-analyst` to enumerate every **root** session (never
   subsessions) whose `working_dir` matches the resolved directory, checking every
   configured server (all-servers completeness applies to the search too, not only
   the deletes). It returns the candidate root sessions and their key facts.
4. **Propose.** For each candidate, build the narrative the same way Flow 2 does
   (see "Summary line" above), present it as a Session Details Block, and add one
   item to a todo list (the `todo` tool) per session found — so every session is
   tracked and none is silently skipped.
5. **Delete all.** Walk the todo list one session at a time. For each: re-run
   `session_summary` (a fresh preview), run the Flow 3 ownership check (a session in
   this folder may not be the user's own), state the impact, confirm explicitly,
   delete, and verify on every server it is on (see Multi-Server Handling) — then
   mark that todo item done. Never mark an item done before its delete is verified.

---

## Flow 2 — Find a Session by Description, Then Delete

1. Take the user's description (topic, content, date range, sometimes a
   server/workspace).
2. **Search.** For any non-trivial criteria (topic, content, date, workspace),
   delegate to `graph-analyst` — it has the data-navigation skills to query the graph
   and returns the candidate session(s): their ids and key facts. This is a data-fetch
   delegation, not a hand-off of the conversation — the results come back to you and
   you keep driving the flow. Skip the delegation only for a trivial direct lookup —
   the user names an exact session id — and call `session_summary` on it directly.
   Cap the candidate set to a handful before per-candidate work.
3. For each candidate, call `session_summary` for the facts, then build the narrative
   yourself (see "Summary line" above; never delegate this part to `graph-analyst`) —
   every candidate gets either a real synthesized narrative or an explicit "not
   available" before it's presented; never a summary written from memory or a prompt
   quote.
4. Build a Session Details Block per candidate and present them; the user picks one.
5. Run the Flow 3 ownership check on the chosen session.
6. Re-run `session_summary` on the chosen id right before delete (a fresh preview, in
   case anything changed since step 3).
7. Confirm explicitly — id, counts, server(s) — then delete and verify on every server
   (see Multi-Server Handling).

---

## Flow 3 — Ownership Check

Runs inside Flow 1, Flow 1-folder, or Flow 2, after the preview and before the delete
confirmation.

1. From the preview, note the session's `created_by`.
2. Call `whoami` for the *same server* the session is on. Read its `contributor_id`.
3. Compare:
   - **Match** → the user's own session. No warning; continue normally.
   - **Differ** → tell the user directly, in your own visible message, that it was
     created by `<created_by>`, not them, and wait for a *separate*, explicit
     confirmation before deleting.
   - **`contributor_id` is null** (auth disabled or unresolvable) → say you can't
     confirm ownership and ask the user directly, waiting for their answer. Don't treat
     null as a mismatch.

Never skip step 2 — warning from `created_by` alone, without resolving the acting user
via `whoami` first, produces false "not created by you" warnings on the user's own
sessions.

---

## Multi-Server Handling (All Flows)

- `list_sources: true` on any tool returns the connectable set: every reachable
  server, with `name`, `url`, `origin` (`source` or `destination`).
- `source=<name>` addresses one server. Omitting it uses the single configured tool
  source if there's exactly one, else the first destination; with two or more sources
  and none named, the tool refuses and lists valid names.
- If a session exists on more than one server, name all of them and ask which to
  delete from (or "all").
- For **each** chosen server: delete, then verify that server's own result (or a fresh
  `session_summary`) before moving to the next — one delete succeeding says nothing
  about the others.
- Report exactly what was removed and from where. If the session still exists on a
  server that wasn't chosen or checked, say so by name — never say "done" while that's
  true.

## Errors to Expect

- **404** — the session id is unknown to that server. Say so; check for a typo or a
  different server.
- **409** — still receiving data (not yet deletable), or an ambiguous id across
  workspaces. Never force or retry aggressively — tell the user it's still live.
- **`ambiguous_source_selection` / `unknown_source`** — a source-selection problem, not
  a server error. Call with `list_sources: true` and ask the user to pick a name.
