"""context_intelligence.reconstruct — session reconstruction utilities.

Public API (imports deferred to Task 9 when implementations land):

    reconstruct_session(session_dir: Path) -> Session
        Rebuild a Session object from a raw session directory on disk.

    load_events(events_path: Path) -> list[Event]
        Parse a JSONL events file into a sequence of typed Event objects.

    extract_transcript(events: list[Event]) -> Transcript
        Distil the conversation transcript from the raw event stream.

    summarise_tools(events: list[Event]) -> ToolSummary
        Produce a summary of tool calls and results from an event stream.

All functions in this subpackage are pure transforms (Level 1) unless they
accept a Path argument, in which case they perform filesystem I/O (Level 3).
Imports are deferred to Task 9 when the implementations are available.
"""
