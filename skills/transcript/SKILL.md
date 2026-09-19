---
name: transcript
description: Retrieve the prior user and assistant conversation verbatim from the current Context Intelligence capture, or named sessions. Use when a user asks to replay, quote, review, or act on a transcript.
version: 1.2.0
license: MIT
user-invocable: true
compatibility: Amplifier with the session_transcript tool mounted
---

# Transcript

Use `session_transcript`; never read `events.jsonl` directly.

Invoking this skill is a request to retrieve the transcript, not to explain how
to retrieve it. Call `session_transcript` in this turn using the arguments below.
Do not stop after loading the skill, present a proposed tool call, or ask "shall
I retrieve it?" The user's request already authorizes this read. Only ask for
ID clarification after the tool reports ambiguity; honor any actual tool
permission gate or explicit user restriction.

> **Sensitive content:** stored captures are replayed verbatim and can contain
> sensitive content. The logging hook's JSON sanitization is not redaction.

Interpret `$ARGUMENTS` as follows:

- **`ID` or `ID -- <intent>`:** a bare full session ID or a hex ID prefix of
  at least 8 characters (for example, `/transcript abcdef12`) selects that
  session. Pass it unchanged in `session_ids`; the tool resolves it. Do not
  ask for the full ID first or treat it as an instruction about the current
  session. With no intent, replay verbatim; with intent, retrieve first, then
  perform that intent.
- **No arguments:** call `session_transcript` with no `session_ids`. Return the
  complete role-marked transcript pages verbatim. Do not summarize or add
  commentary.
- **Intent only:** call `session_transcript` with no `session_ids`, page through
  the whole transcript, then perform the requested intent using it as source
  material. State that the answer was based on the native transcript.
- **`--session ID[,ID...]`:** retrieve those sessions in the listed order. With
  no further intent, replay each transcript verbatim under its own session
  heading.
- **`--session ID[,ID...] -- <intent>`:** retrieve all named sessions first,
  then perform the intent using those transcripts.

A bare token selects a session only when it actually looks like a capture ID:
eight or more hex characters (`abcdef12`, a full UUID), or the zero-padded
sub-agent form `0000000000000000-<hex>[_<agent>]`. Anything else is intent about
the current session, even when it contains a hyphen, underscore, or digit —
`chapter-2` and `summarize-day-3` are intents, not IDs. When a token could be
read either way, treat it as intent: a wrong intent is visible and recoverable,
a wrong lookup just fails. For an opaque ID that does not match those shapes,
use explicit `--session`, which accepts unique prefixes and exact opaque IDs.
If natural language explicitly names a session, pass that ID or prefix to the
tool as well.

On an ambiguous prefix, show the tool's candidates and pass on the length it
reports as needed — it names the character position where the candidates
diverge, so relay that rather than asking vaguely for "a longer prefix". On a
missing session, report that failure. Never choose a candidate, retry against
the current session, or delegate to an agent to guess an ID.

A session whose capture directory exists but has no readable `metadata.json` is
reported as `capture_unavailable`, and an ID whose only captures are derived
`<id>_<agent>` sub-agent sessions is reported as `session_not_found` listing
those sub-agents. Neither is a prompt to substitute one of them; report what the
tool said.

For a single session, keep calling `session_transcript` with its returned
full `session_id` and `next_after_event_line` as the next `after_event_line`
while `has_more` is true. For multiple sessions, retrieve and
page one session at a time. For a batched follow-up, replace the original prefixes
in `session_ids` with returned full IDs and use those same full IDs as
`after_event_lines` keys. A page boundary is normal; preserve the tool's
`[MORE MESSAGES AVAILABLE ...]` marker between pages. Do not hide capture
issues, infer missing messages, use graph reconstruction, or fall back to raw
provider events.
