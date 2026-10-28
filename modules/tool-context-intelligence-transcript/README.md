# Context Intelligence transcript tool

This Amplifier tool module mounts only `session_transcript`, which reads bounded,
role-marked user and assistant text from a native Context Intelligence capture or
an explicitly configured Foundation-persisted conversation.
It does not mount graph or blob tools.

Stored captures are replayed verbatim and can contain sensitive content. The logging
hook's JSON sanitization is not redaction.

## Trusted configured captures

The optional **module mount config** `capture_roots` defaults to `{}`. It lets a
trusted embedding host grant reads of exact native capture directories outside
the normal capture store. For app-cli overrides, place it under
`overrides.tool-context-intelligence-transcript.config`:

```yaml
capture_roots:
  review_copy:
    directory: /srv/captures/review-copy
    session_id: example-session-full-id
```

Each entry requires `directory` (an absolute native directory) and
`session_id` (the expected full opaque ID, never prefix-resolved). The only optional
entry key is trusted `capture_format`; it is frozen with the grant:

| `capture_format` | Files read | Metadata contract |
|------------------|------------|-------------------|
| `context-intelligence` (default) | `metadata.json`, `events.jsonl` | Native CI format and schema `1.0.0`, exact session ID and string workspace. |
| `amplifier-transcript` (explicit opt-in) | `metadata.json`, `transcript.jsonl` | JSON object with exact expected session ID; workspace is optional. No CI format/version marker is required. |

For example, add `capture_format: amplifier-transcript` to `review_copy` to read
that profile. This value is **mount config**, never a tool argument; the existing
tool `format` argument still selects only output presentation (`text` or `json`).
Unknown/malformed configured formats fail mount before filesystem access. There
is no automatic detection, cross-format fallback, backup recovery, or directory
discovery. Only the selected literal primary and metadata are opened. Foundation
owns persistence; this reader does not import CLI storage or rendering behavior,
construct a history store, or define a new versioned Foundation schema.

The directory name need not match the session ID. Relative paths, URI paths, dot components, symlinks (including
ancestors), and nonregular files are refused. Configured directories must exist
at mount. Invalid config fails mount loudly, rather than silently disabling it.
Validated grants are frozen for the tool's lifetime, and the directory device/inode
identity is pinned at mount and checked on every read. Changing a grant requires
a new mount; mutating the original config does not authorize another directory.

This is a restricted local read grant, **not** a tool argument that grants arbitrary
filesystem access. It does not move the hook's base path, discover directories,
register copies in a host store, or fall back to another resolver. The opt-in
route requires POSIX descriptor-relative `os.open`, `O_DIRECTORY`, `O_NOFOLLOW`,
and `O_NONBLOCK`. On unsupported platforms (including Windows), nonempty config
or an existing configured read fails closed with `configured_capture_unsupported`.
Ordinary current-session, ID/prefix, custom-host resolver and hook resolver reads
remain unchanged. No Windows secure-open backend is provided.

## Directed reads and pagination

Aliases match `^[A-Za-z][A-Za-z0-9_-]{0,63}$` (1–64 ASCII characters). Call:

```json
{"capture_root":"review_copy","max_messages":1,"format":"json"}
```

Only one alias is allowed per call. Do not include `session_ids` or
`after_event_lines`, even as `null` or empty values. For the next page, keep the
alias and use that page's `next_after_event_line` as the scalar `after_event_line`:

```json
{"capture_root":"review_copy","after_event_line":1,"max_messages":1,"format":"json"}
```

Do **not** substitute the returned full session ID: the normal store may contain
a different copy with that same ID. Every configured page retains the normal full
ID and cursor fields plus:

```json
{"source":{"kind":"configured_capture","capture_root":"review_copy","capture_directory":"/srv/captures/review-copy"}}
```

`capture_directory` is the canonical absolute, non-symlink directory, so the result
also discloses that trusted configured path. Limits remain 200 messages and 100,000
content characters per tool call. Messages are not split or silently truncated.
An individual message exceeding the requested budget returns `message_too_large`.
Malformed or oversized event lines produce explicit `partial` pages with issues;
a filled page alone is normal successful pagination. Both JSON and text preserve
the original message strings. Provider-raw and tool payloads are not replayed.

## Persisted conversation text projection

The `amplifier-transcript` profile reads bounded JSONL message objects with stored
roles. Only `user` and `assistant` text is returned. These role labels are context
provenance, **not proof that a person authored the text**. System, developer and
tool messages are omitted. Known `tool_use`, `tool_call`, `tool_result`, `thinking`,
`redacted_thinking`, `reasoning`, `image` and `image_url` blocks are omitted without
walking their payloads. Other provider/continuation fields and tool-call arguments
are never returned. Missing/null assistant content is accepted as tool-only only
with a nonempty compatibility `tool_calls` list of objects; otherwise it is an issue.

String content (including empty strings, whitespace and newlines) is preserved
exactly. For ordered typed text blocks, JSON `content` is a list of safe
`{"index":0,"text":"original string"}` parts, retaining each original zero-based
block index. No semantic join is invented. Text rendering labels each block
boundary outside the unchanged stored string. Character budgets count the sum
of all returned text parts. Pagination is between **entire stored messages**, not
individual blocks. The tool still caps content at 100,000 characters per call.

Each persisted page adds payload-free `projection.omitted_messages` and
`projection.omitted_blocks` counts for that page's consumed records. Intentional
nontext omissions are not capture defects. Unknown roles/parts, invalid flag
values, malformed message/JSON/UTF-8 records and physical lines over 1,000,000 bytes
produce fixed safe issues and `partial` status. Independently valid text parts
are retained, without claiming the full structured message was projected.
Unknown metadata fields do not certify or select a format.

Stored reminder text is **not** display-filtered. Boolean `metadata.ephemeral`
and `metadata.persisted` values, when present, appear in safe message
`context_flags` and text provenance labels. Other metadata is not returned.
This differs intentionally from the CLI's user-facing display policy.
Absent timestamps are `null` and generate no invented time marker. Absent
workspace and schema are `null`; invalid workspace produces a safe issue.
The renderer identifies `transcript.jsonl` as a stored text projection, not a
native CI event stream.

Persisted messages use `source_event: transcript:message`. Their page `source`
adds `source_format: amplifier-transcript`, `source_file: transcript.jsonl`, and
`cursor_unit: physical_line`. The compatibility field names `event_line`,
`after_event_line`, and `next_after_event_line` count physical lines in the
**selected file**. Continue with the same alias and scalar cursor. These extra
projection/provenance fields are not added to native CI pages.

## Error and privacy contract

The configured route returns fixed safe messages, without echoing rejected paths,
metadata values, alias input or event fragments. Relevant error types:

| Type | Meaning |
|------|---------|
| `invalid_capture_config` / `configured_capture_unsupported` | Invalid trusted mount config or unavailable secure-open capability; mount fails. |
| `invalid_request` / `configured_capture_unknown` | Invalid/mixed selectors or alias absent from this mount; no resolver fallback. |
| `configured_capture_changed` / `configured_capture_identity_mismatch` | Directory identity changed since mount or metadata differs from the expected full ID. |
| `configured_capture_unavailable` / `configured_capture_unsafe` | Missing, denied, unreadable, symlinked or nonregular capture files/directories. |
| `metadata_unavailable` / `unsupported_capture_format` / `unsupported_schema_version` | Metadata exceeds 64 KiB, is not UTF-8 JSON with required fields, or is not native format/version `1.0.0`. |

Metadata identity and the selected profile are validated **before** opening content. No configured
read writes files or contacts a server. Descriptor safety prevents path retargeting;
it does not make maliciously writable backing files immutable or certify their
contents. Captures are stored and replayed verbatim, not redacted. Authorize only
captures the tool's caller may read; do not assume results are safe to share.