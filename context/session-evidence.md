# Session evidence routing

For user/assistant conversation, use `session_transcript` when mounted. For local
event, tool, error or capture questions, use `session-navigator`; for graph
relationships, aggregates or blob resolution, use `graph-analyst`. Delegate only
when permitted and available; specialists perform their matching work themselves.
Transcript and local lookups need no graph health check. Inspect mounted
capabilities before reporting access unavailable.

Use only session, workspace and server scope covered by the request or assignment;
an identifier alone grants no access. Keep results bounded and disclose only needed
content: captures may contain secrets. State the evidence source, coverage and
observed freshness. Capture, forwarding and indexing are distinct; a miss in one
layer does not establish absence elsewhere. Historical evidence is not
authoritative live state or permission.