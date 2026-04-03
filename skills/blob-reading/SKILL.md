---
name: blob-reading
version: 1.0.0
description: Safe resolution of ci-blob:// URIs — extract specific fields without dumping full payloads
license: MIT
---

# Blob Reading

`ci-blob://` URIs appear in graph query results when a payload is too large to inline. This skill covers safe resolution patterns to extract only the data you need.

---

## When to Use blob_read

Use `blob_read` only when you need a **specific payload** from a graph node. Most Cypher queries return structured metadata that is sufficient without resolving any blobs.

**Do resolve a blob when:**
- You need the full `input` or `output` of a specific tool call
- You need the `content` of a specific assistant turn
- You need a specific field that is stored as a blob URI

**Do NOT resolve a blob when:**
- You are counting events, listing sessions, or summarizing metadata
- The graph query already returned the structured fields you need
- You are doing broad exploration — scan metadata first, resolve only if required

> Most queries don't need blobs. Always exhaust what the graph returns before resolving.

---

## How to Resolve a Blob

Follow these four steps to safely resolve a `ci-blob://` URI:

**Step 1 — Identify the URI**
Locate the `ci-blob://` URI in the graph query result. It will appear as a string value in a node property.

**Step 2 — Call `blob_read`**
Pass the full URI to the `blob_read` tool:
```
blob_read(uri="ci-blob://session_id/key")
```

**Step 3 — Get the file path back**
`blob_read` returns a local file path where the blob content has been written. The file is temporary and exists only for this session.

**Step 4 — Read selectively**
Use `jq`, `head`, or targeted shell commands to extract only the field(s) you need. Never read the full file into context.

---

## Safe Extraction Patterns

Always check size before reading, then extract only the field you need.

**Check size before reading:**
```bash
ls -lh /tmp/blob_abc123.json
wc -c /tmp/blob_abc123.json
```

**Extract a top-level field:**
```bash
jq '.field_name' /tmp/blob_abc123.json
```

**Check available keys (safe exploration):**
```bash
jq 'keys' /tmp/blob_abc123.json
```

**Extract a nested field:**
```bash
jq '.response.content[0].text' /tmp/blob_abc123.json
```

**Extract with a size guard (first 500 chars):**
```bash
jq -r '.response.content[0].text' /tmp/blob_abc123.json | head -c 500
```

---

## Critical Rules

1. **Never dump the full blob** — do not `cat` or `read_file` the entire blob path into context. A single blob can contain 100k+ tokens.

2. **Always check the `ci-blob://` prefix** — only strings that start with `ci-blob://` are blob URIs. Do not pass other strings to `blob_read`.

3. **Prefer targeted extraction** — use `jq '.specific_field'` rather than reading the whole structure.

4. **Always check size first** — run `ls -lh` or `wc -c` on the file path before extracting content. If the file is very large, be even more selective.

5. **File path is temporary** — the path returned by `blob_read` is a temporary file for this session only. Do not persist or reference it across sessions.

---

## Blob Field Detection

Blob URIs appear as string values in graph node properties. They look like this in raw JSON:

```json
{
  "event_id": "evt_abc123",
  "type": "tool_result",
  "data": "ci-blob://session_abc/tool_result_payload_xyz"
}
```

**To detect and resolve:**
1. Parse the node result as JSON first
2. Check if the `data` field (or whichever field is relevant) starts with `ci-blob://`
3. Only then call `blob_read` on that URI

> Not every `data` field contains a blob. Small payloads are inlined as plain JSON — only large payloads are stored as `ci-blob://` URIs. Always check the prefix before calling `blob_read`.
