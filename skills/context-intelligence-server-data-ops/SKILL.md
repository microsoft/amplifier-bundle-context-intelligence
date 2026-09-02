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
  session's `created_by` (see Ownership Check).

All three accept `source` (name a server) and `list_sources: true` (discover the
connectable set without acting). None takes a workspace — you always address a
session by id.

- **`todo`** — the standard todo-list tool. Used only in the folder cleanup, one item
  per candidate root session found, so a bulk cleanup never silently skips one.

A lockdown hook denies this agent write_file, edit_file, apply_patch, and any direct
graph-query tool — it guides the user through settings edits instead of making them,
and delegates all search and narrative work to `graph-analyst`.

---

## Key Concepts

**Current session id.** Comes from Amplifier's own runtime context — the `Session
ID` field injected into your status context every turn. For any "this session" / "my
current session" request, that value IS the session to act on. Don't ask the user
for an id, and a typed id never replaces it — resolve from context regardless. Only
ask directly if context genuinely has no `Session ID`.

**Current working directory (for the folder cleanup).** Comes from the same runtime
context — the `Working directory` field injected into your status context every
turn, resolved the same way as the session id above. For any "this folder" / "this
working directory" / "uploaded from here" request, that value is the directory to
search — not a single session id. Don't ask unless context genuinely has none.

**Current user identity (for ownership).** Never read from context and never guess.
Call `whoami` for the *same server* the session in question is on, and use its
`contributor_id` as the one reference identity for the comparison (see Ownership
Check).

**When the folder exclusion applies.** Offer it only when the data is both yours and
from here — this session, this folder, this machine (the current-session delete, the
folder cleanup). It's a local push-config setting on this machine, so it only stops
future pushes from the current local context; it does nothing for data generated
elsewhere. Don't offer it for a session found by topic/description (the
find-by-description delete) or one that isn't yours (see Ownership Check).

## The "Session Details" Block

Use this exact shape for any candidate or confirmed target (the find-by-description
candidate list; the pre-delete confirmation in any flow):

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

**Summary line.** Not returned by the server, and not built by this agent — it comes
from `graph-analyst`, delegated to alongside the search (see the search step in
"Find a Session by Description, Then Delete"). It returns a short synthesized
overview of what the session was about, built from that session's **root**-session
prompts only (never subsessions). This must be a synthesis, never a raw or verbatim
prompt quote, and never a from-memory guess. If `graph-analyst` can't produce one,
use "not available."

---

## Delete the Current Session

Applies whenever the request refers to the user's own current session ("my current
session," "this session," "the session I'm in") — even if the user also supplies a
session id; a supplied id doesn't downgrade it out of this case or replace the runtime
id. Route to **Clean Up Everything Pushed From This Working Directory** instead when
the request is about the whole working directory, not just the current session
("this folder," "this working directory," "uploaded from here"). Route to **Find a
Session by Description, Then Delete** when the request names or searches for some
*other* session.

1. **Resolve.** Take the current session id from runtime context (see Key Concepts).
   Don't ask the user for an id.
2. **Prove.** Call `session_summary(session_id=<resolved>, list_sources=true)` and show
   the user: `root_id`, `created_by` (confirmed against `whoami` for the same server),
   `working_dir`, and `last_change` (flag if under a minute old — "may still be live").
   Note every server the session exists on, from `list_sources`. If it 404s on every
   server, stop and tell the user plainly — never delete against an unresolved session.
3. **Offer the folder exclusion to the user directly, and wait for their answer**,
   before anything else proceeds. This is the destination **push filter** on the
   session's `working_dir`, not the delete scope — deletion always removes the whole
   session graph regardless of this setting. Show the setting
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

## Find a Session by Description, Then Delete

1. Take the user's description (topic, content, date range, sometimes a
   server/workspace).
2. **Search + narrate.** For any non-trivial criteria (topic, content, date, workspace),
   delegate to `graph-analyst`. It runs the search AND returns, for each candidate, both
   the key facts and a short synthesized overview (see "Summary line" above) — you never
   query the graph or build the narrative yourself. This is a data-fetch delegation, not
   a hand-off of the conversation — the results come back to you and you keep driving
   the flow. Skip the delegation only for a trivial direct lookup — the user names an
   exact session id — and call `session_summary` on it directly. Cap the candidate set
   to a handful before per-candidate work.
3. Build a Session Details Block per candidate, using the overview `graph-analyst`
   returned (or "not available"), and present them; the user picks one.
4. Run the ownership check on the chosen session.
5. Re-run `session_summary` on the chosen id right before delete (a fresh preview, in
   case anything changed since step 2).
6. Confirm explicitly — id, counts, server(s) — then delete and verify on every server
   (see Multi-Server Handling).

---

## Clean Up Everything Pushed From This Working Directory (Yours, From Here)

This is a find-by-criteria cleanup, not a current-session delete. The
current-session delete's defining trait is the **current** session, resolved from
runtime; this cleanup finds sessions by **criteria** — `working_dir` = this folder
AND `created_by` = you — the same find-by-criteria shape as the find-by-description
delete. The only thing it borrows from the current-session delete is the
folder-exclusion offer, because it's your own folder being pushed.

Applies when the user is worried that data from sessions run in THIS folder was
pushed and should not have been — "I think I uploaded data from sessions in this
folder, I want to delete it," "things from this working directory should never
have been pushed." Route here instead of the current-session delete when the
request is about the *folder*, not a single session; route here instead of the
plain find-by-description delete when the criteria is specifically "this folder +
mine" rather than a topic/date/server description.

1. **Apply the folder exclusion first**, before finding or deleting anything.
   Resolve the working directory from runtime context (see Key Concepts), the
   same way the current-session delete resolves the current session id. Offer the
   setting `overrides.hook-context-intelligence.config.destinations.<name>.exclude`
   in `~/.amplifier/settings.yaml` — the same folder-exclusion offer described in
   the current-session delete's step 3 — show it in your own message, guide the
   user through applying it (you never edit the file yourself), and confirm it's
   applied before moving on: while the folder is still in scope, continued
   ingestion would keep re-creating the very data you are about to delete.
2. **Run the S2 search, by criteria (this folder + mine).** Delegate to
   `graph-analyst` to enumerate every **root** session (never subsessions)
   whose `working_dir` matches the resolved directory AND `created_by` is you,
   checking every configured server (all-servers completeness applies to the
   search too, not only the deletes). It returns the candidate root sessions,
   their key facts, and a short synthesized overview for each — same as the
   find-by-description delete's search step.
3. **Propose the list.** For each candidate, build a Session Details Block
   using the overview `graph-analyst` returned (see "Summary line" above),
   present it, and add one item to a todo list (the `todo` tool) per session
   found — so every session is tracked and none is silently skipped.
4. **Delete all.** Walk the todo list one session at a time. For each: re-run
   `session_summary` (a fresh preview), run the ownership check (a
   session in this folder may not be the user's own), state the impact,
   confirm explicitly, delete, and verify on every server it is on (see
   Multi-Server Handling) — then mark that todo item done. Never mark an item
   done before its delete is verified.

---

## Ownership Check (Before Deleting Any Found or Named Session)

Runs inside any of the delete flows above — the current-session delete, the
find-by-description delete, or the folder cleanup — after the preview and before
the delete confirmation.

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
