---
name: transcript
description: Retrieve the prior user and assistant conversation verbatim from the current Context Intelligence capture, or named sessions. Use when a user asks to replay, quote, review, or act on a transcript.
version: 1.1.0
license: MIT
user-invocable: true
compatibility: Amplifier with the session_transcript tool mounted
---

# Transcript

Use `session_transcript`; never read `events.jsonl` directly.

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

Bare opaque ID tokens containing a hyphen, underscore, or digit also select a
session; for other non-UUID references use explicit `--session`, which accepts
unique prefixes and exact opaque IDs.
If natural language explicitly names a session, pass that ID or prefix to the
tool as well. Ordinary intent text such as `summarize` still targets the
current session; it is not a session ID.

On an ambiguous prefix, show the tool's candidates and ask for a longer prefix
or full ID. On a missing session, report that failure. Never choose a candidate,
retry against the current session, or delegate to an agent to guess an ID.

For a single session, keep calling `session_transcript` with its returned
full `session_id` and `next_after_event_line` as the next `after_event_line`
while `has_more` is true. For multiple sessions, retrieve and
page one session at a time. For a batched follow-up, replace the original prefixes
in `session_ids` with returned full IDs and use those same full IDs as
`after_event_lines` keys. A page boundary is normal; preserve the tool's
`[MORE MESSAGES AVAILABLE ...]` marker between pages. Do not hide capture
issues, infer missing messages, use graph reconstruction, or fall back to raw
provider events.
